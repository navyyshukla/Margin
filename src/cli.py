"""margin — compress a JSON API response on its way into an LLM prompt.

The compressor was a library with a test harness around it and nothing that
used it: `python src/compress.py f.json > out.txt`, then open the file and copy
it by hand. This is the tool. It reads a path or stdin, puts the document on
stdout and everything else on stderr, so the thing it was built for works:

    curl -s https://api.github.com/repos/python/cpython/issues | margin | pbcopy

That one pipeline is what fixes the shape of everything here.

**stdout carries the document and nothing else.** Notes, warnings and the
savings summary go to stderr — not as tidiness, but because anything else ends
up on the clipboard. `src/cli_test.py` asserts it by decompressing stdout alone.

**"Unchanged" means byte-for-byte unchanged.** Two paths hand the input back:
a payload no rule improves (Open-Meteo, exchange rates) and input that is not
JSON at all. Both used to be approximate — the JSON path added a trailing
newline the file never had, so a note reading "returned the input unchanged"
was off by a byte. A filter you can drop into an arbitrary pipeline has to mean
that literally, so this operates on bytes at the boundary and emits exactly what
it read. (It also means input that is not even valid UTF-8 survives intact
rather than crashing or being re-encoded.)

**Not JSON is not an error.** An HTML error page from a failed curl exits 0 and
passes through, because the output is correct — margin handed back what it was
given. A non-zero exit would break `set -e` for a non-error and would not stop
`pbcopy` anyway, which runs regardless. What stops you pasting an error page
unnoticed is the line on stderr, so that line is loud and never suppressed by
anything but -q.

Exit codes: 0 = output on stdout is what you want. 2 = margin was called wrong,
or there was nothing to read. There is no code for "compressed poorly" — see
docs/cli.md.

Usage:
    margin f.json          margin < f.json          curl ... | margin
    margin -               margin -q f.json
"""

import signal
import sys

import render
import store as store_module
from compress import compress_json, token_count
from detect import detect_content_type

USAGE = ("usage: margin [-q] [--store] [<file.json>|-]   (with no path, reads stdin)")

# --store is OFF by default, and that is the whole design of this flag.
#
# The everyday invocation is `curl ... | margin | pbcopy`, and what comes out of
# it gets pasted into a chat. A document with `\@0001` handles in it is only
# readable by a client that can reach this machine's store through the MCP
# server -- paste it anywhere else and the model does not merely lack the
# store, it has no fetch tool at all, so the handles are dead and the values
# they stand for are silently missing at exactly the moment someone is relying
# on them.
#
# So storing is valid only when a resolver is in the loop, and the default path
# keeps producing a self-contained document, byte for byte what it produced
# before the store existed.

EXIT_USAGE = 2
# 2, not 1: 1 is what an uncaught Python traceback already exits with, and
# "margin was called wrong" should be distinguishable from "margin crashed".


class CliError(Exception):
    """Something to report in one line and exit on, rather than traceback at.

    A missing file used to print eleven lines of FileNotFoundError traceback.
    That is the right output for a bug in margin and the wrong output for a
    typo in a filename, and the two need to look different.
    """


def read_payload(args):
    """The exact bytes to compress, from a path or stdin.

    Bytes, not text, and the same on both routes — decoding happens once, later,
    in one place. Reading a file as text and stdin as text would give two chances
    to differ, and `margin f.json` and `cat f.json | margin` producing identical
    output is the property the whole pipe rests on.
    """
    if len(args) > 1:
        raise CliError(f"one payload at a time — got {len(args)}\n{USAGE}")

    if not args or args[0] == "-":
        # No path and stdin is a terminal means nobody piped anything in.
        # Reading would block on the tty and look exactly like a hang, so say
        # what is wrong instead. `margin -` is explicit and skips this.
        if not args and sys.stdin.isatty():
            raise CliError(f"no input — give a path or pipe something in\n{USAGE}")
        return sys.stdin.buffer.read()

    try:
        with open(args[0], "rb") as f:
            return f.read()
    except OSError as exc:
        raise CliError(f"cannot read {args[0]}: {exc.strerror}") from exc


def parse_args(argv):
    """Split argv into (paths, quiet, wants_help). Raises CliError on anything
    unknown.

    An unrecognised flag is an error rather than a filename. Without this,
    `margin --verbose` reports "cannot read --verbose: No such file or
    directory", which describes what happened and not what went wrong.

    Help is reported back rather than exited on here, because asking for it is
    not a failure: it belongs on stdout with exit 0, and every exit inside this
    function is a 2.
    """
    quiet = False
    wants_help = False
    use_store = False
    paths = []
    for arg in argv:
        if arg in ("-q", "--quiet"):
            quiet = True
        elif arg in ("-h", "--help"):
            wants_help = True
        elif arg == "--store":
            use_store = True
        elif arg != "-" and arg.startswith("-"):
            raise CliError(f"unknown option {arg}\n{USAGE}")
        else:
            paths.append(arg)
    return paths, quiet, wants_help, use_store


def store_summary(held):
    """The second half of the truth, when a store is in use.

    "93.7% saved" on its own is a lie of omission once content moves to disk.
    The old number meant the model reads that much less; this one means that
    much went somewhere the model must go and fetch. Reporting one figure would
    let the headline number quietly change meaning — the exact failure
    docs/shapes.md records twice — so the values held are stated beside it and
    the percentage is labelled "in the prompt" rather than "saved".
    """
    return f"{held} value(s) held in the store — fetchable, not discarded"


def summary(raw_text, out_text, stored=False):
    """The one line worth seeing on every run: what this cost and what it saved.

    `stored` changes the wording, not the arithmetic, and it has to. Without a
    store "55.3% saved" means the reader reads that much less. With one,
    "93.4%" means that much went somewhere the reader must go and fetch, and
    calling both "saved" would let the headline number change meaning while
    looking the same -- which is precisely the failure docs/shapes.md records
    twice. So it reads "in the prompt", and store_summary() states what is
    being held.

    Free, because compress_json has already counted both of these strings and
    token_count memoises — measured 16 ms per uncached count of the largest
    sample (github_issues.json, 50,031 tokens) against 42 ms for the whole
    compression, so paying it twice more would have been a third of the runtime
    spent on a status line.
    """
    before = token_count(raw_text)
    after = token_count(out_text)
    saved = 1 - after / before
    label = "in the prompt" if stored else "saved"
    return f"{before:,} → {after:,} tokens ({saved:.1%} {label})"


def main(argv=None):
    """Returns an exit code. Every write to stdout in here goes through
    sys.stdout.buffer: the document is bytes we either read or encoded
    ourselves, and print() would append newlines to output that must not have
    them."""
    argv = sys.argv[1:] if argv is None else argv

    def note(message):
        if not quiet:
            print(f"margin: {message}", file=sys.stderr)

    quiet = False
    use_store = False
    try:
        paths, quiet, wants_help, use_store = parse_args(argv)
        if wants_help:
            print(USAGE)  # stdout, exit 0: asking for help is not a failure
            return 0
        raw_bytes = read_payload(paths)
    except CliError as exc:
        print(f"margin: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not raw_bytes:
        # Almost always a failed curl upstream. Passing zero bytes through
        # silently is how that reaches the clipboard as an empty paste.
        print("margin: empty input — nothing to compress", file=sys.stderr)
        return EXIT_USAGE

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        note("not UTF-8 — passed through unchanged")
        sys.stdout.buffer.write(raw_bytes)
        return 0

    # Two guards, and neither contains a write to stdout.
    #
    # Both recursions in the pipeline have to be covered, because either can
    # blow the stack and they are on opposite sides of the same call. json.loads
    # raises RecursionError itself past roughly 3× the recursion limit, from
    # inside detect_content_type, which catches only JSONDecodeError; below
    # that, the parse succeeds and strip_boilerplate blows up instead. Guarding
    # only compress_json left the first live; guarding them together, with a
    # test payload deep enough to reach the parser, silently stopped exercising
    # the second — the pipeline short-circuits at whichever recursion comes
    # first, so one guard tested by one payload can only ever cover one half.
    #
    # `except Exception`, not `except RecursionError`: the promise is about the
    # pipeline, not about which bugs were anticipated. This is the library's own
    # rule — "if the table cannot be proved correct, emit JSON" — one layer up.
    #
    # The writes stay outside. With the passthrough write inside the guard, a
    # partial write that raised (a full disk, mid-flush) would land in the
    # handler and emit raw_bytes a second time, appending a duplicate copy to
    # what was already written and printing two contradictory notes.
    try:
        content_type, data = detect_content_type(raw_text)
    except Exception as exc:
        note(f"could not parse ({type(exc).__name__}: {exc}) — passed through unchanged")
        sys.stdout.buffer.write(raw_bytes)
        return 0

    if content_type != "json":
        # No summary line here. Its percentage would be 0.0% by construction —
        # it can say nothing else — and computing it is what drags tiktoken onto
        # the one path whose entire promise is "handed back exactly what you
        # gave me". On a machine with no tiktoken cache that means fetching a
        # BPE vocabulary over the network to print a constant, so a failed curl
        # on a fresh laptop would traceback instead of passing through. The
        # not-UTF-8 branch above already prints no summary; now they agree.
        note("not JSON — passed through unchanged")
        sys.stdout.buffer.write(raw_bytes)
        return 0

    # Constructing a FileStore touches no disk; a store that cannot be written
    # fails at put() time, inside the guard below, which passes the input
    # through unchanged and says what broke. That is the right outcome and a
    # loud one -- what must never happen is quietly inlining the cells and
    # reporting a saving as though the store had worked.
    backing = store_module.FileStore(store_module.default_root()) if use_store else None

    try:
        text, notes = compress_json(data, original_text=raw_text, store=backing)
    except Exception as exc:
        note(f"could not compress ({type(exc).__name__}: {exc}) — passed through unchanged")
        sys.stdout.buffer.write(raw_bytes)
        return 0

    for message in notes:
        note(message)

    # Counted from the document rather than from the store, because the document
    # is what the reader gets: a store that wrote ten objects while the document
    # kept none of them would otherwise be reported as a saving.
    held = 0
    if use_store and text.startswith(render.FORMAT_MARKER):
        held = len(store_module.used_ids(render.parse(text)))

    note(summary(raw_text, text, stored=bool(held)))
    if held:
        note(store_summary(held))

    if text == raw_text:
        # compress_json declined to improve this one. Emit the bytes we read,
        # not a re-encoding of them — see the module docstring.
        sys.stdout.buffer.write(raw_bytes)
    else:
        document = text if text.endswith("\n") else text + "\n"
        sys.stdout.buffer.write(document.encode("utf-8"))
    return 0


def die_on_broken_pipe():
    """Behave like every other Unix filter when the reader goes away.

    Python installs its own SIGPIPE handler, which turns the signal into a
    BrokenPipeError — so `margin f.json | head -1`, which is how you look at a
    compressed document, printed a five-line traceback under the output. Found
    by using the tool, which is the entire argument for building it before the
    next phase.

    SIG_DFL rather than catching the exception: the exception arrives at
    whichever write happens to be unlucky, and Python then prints a second
    complaint from its shutdown flush that no try/except in main() can reach.
    Restoring the default handler makes the process die on the signal the way
    `cat` does — silently, and with the 141 a shell expects.
    """
    if hasattr(signal, "SIGPIPE"):  # absent on Windows; this is a macOS tool
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)


if __name__ == "__main__":
    die_on_broken_pipe()
    sys.exit(main())
