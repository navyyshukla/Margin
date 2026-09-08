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

from compress import compress_json, token_count
from detect import detect_content_type

USAGE = "usage: margin [-q] [<file.json>|-]   (with no path, reads stdin)"

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
    paths = []
    for arg in argv:
        if arg in ("-q", "--quiet"):
            quiet = True
        elif arg in ("-h", "--help"):
            wants_help = True
        elif arg != "-" and arg.startswith("-"):
            raise CliError(f"unknown option {arg}\n{USAGE}")
        else:
            paths.append(arg)
    return paths, quiet, wants_help


def summary(raw_text, out_text):
    """The one line worth seeing on every run: what this cost and what it saved.

    Free, because compress_json has already counted both of these strings and
    token_count memoises — measured 16 ms per uncached count of the largest
    sample (github_issues.json, 50,031 tokens) against 42 ms for the whole
    compression, so paying it twice more would have been a third of the runtime
    spent on a status line.
    """
    before = token_count(raw_text)
    after = token_count(out_text)
    saved = 1 - after / before
    return f"{before:,} → {after:,} tokens ({saved:.1%} saved)"


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
    try:
        paths, quiet, wants_help = parse_args(argv)
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

    content_type, data = detect_content_type(raw_text)
    if content_type != "json":
        # No summary line here. Its percentage would be 0.0% by construction —
        # it can say nothing else — and computing it is what drags tiktoken
        # onto the one path whose entire promise is "handed back exactly what
        # you gave me". On a machine with no tiktoken cache that means fetching
        # a BPE vocabulary over the network to print a constant, so a failed
        # curl on a fresh laptop would traceback instead of passing through.
        # The not-UTF-8 branch above already prints no summary; now they agree.
        note("not JSON — passed through unchanged")
        sys.stdout.buffer.write(raw_bytes)
        return 0

    try:
        text, notes = compress_json(data, original_text=raw_text)
    except Exception as exc:
        # The library's own standing rule is "if the table cannot be proved
        # correct, emit JSON". This is that rule one layer up: if the payload
        # cannot be compressed at all, emit the payload.
        #
        # Bought by a real one. json.loads parses 3,000 nested arrays happily
        # in its C scanner, then strip_boilerplate recurses through them and
        # blows the stack — and RecursionError is not a JSONDecodeError, so it
        # escaped detect_content_type and this function both. Exit 1, traceback
        # on stderr, and ZERO BYTES on stdout: `curl ... | margin | pbcopy`
        # silently replaced the clipboard with nothing.
        #
        # Catching Exception rather than RecursionError on purpose. The
        # promise this file makes is about the pipeline, not about which bugs
        # were anticipated: whatever goes wrong in there, the bytes you handed
        # over come back out and stderr says what happened.
        note(f"compression failed ({type(exc).__name__}: {exc}) — passed through unchanged")
        sys.stdout.buffer.write(raw_bytes)
        return 0

    for message in notes:
        note(message)
    note(summary(raw_text, text))

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
