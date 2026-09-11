"""Does the CLI keep the promises the CLI makes?

The library already has two gates. Neither can see this layer: eval_harness.py
and property_test.py both call compress_json in-process, so no argument, no
stream and no exit code has ever been checked by anything.

**The naive version of this file is worthless, and it is worth saying why.**
"exits 0 and stdout parses as JSON" is passed by a CLI that is `cat`. Every one
of the eight sample payloads satisfies it — two of them are *supposed* to come
back byte-identical, so on those a do-nothing CLI is indistinguishable from a
correct one. That is Rule 1 (a test of a format must assert the format was
used) one layer up: a test of a tool must assert the tool ran.

So every check below is aimed at a specific sentence in cli.py's docstring:

    "stdout carries the document and nothing else"
        -> stdout ALONE, stderr discarded, decompresses back to the payload
    "unchanged means byte-for-byte unchanged"
        -> cmp against the input bytes, not a rstrip'd comparison
    "not JSON is not an error"
        -> exit 0, and the stderr line that makes it survivable is present
    stdin and a path are one input path
        -> the two produce byte-identical stdout

Run: python src/cli_test.py
"""

import json
import os
import signal
import subprocess
import sys
import tempfile

import render
from compress import compress_json, strip_boilerplate
from decompress import decompress
from property_test import repeated
from table import same_json

# Relative to THIS file, never to the repo. .githooks/pre-commit runs the
# STAGED tree out of a scratch directory (git checkout-index) precisely so it
# tests what is being committed rather than what is lying in the working tree.
# A test that shelled out to a hardcoded repo path would quietly validate the
# wrong code and hand the gate back its own answer. sys.executable for the same
# reason: it is the interpreter running this file, not a guess at where a venv
# lives.
SRC = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(SRC, "cli.py")
WRAPPER = os.path.join(os.path.dirname(SRC), "bin", "margin")

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        failures.append(f"{name}{': ' + detail if detail else ''}")


def run(args, stdin=b""):
    """Invoke the CLI as a subprocess. Returns (exit_code, stdout, stderr).

    A subprocess, not a call to cli.main(): argv parsing, the stdout/stderr
    split and the exit code only exist at the process boundary, and importing
    main() would test everything except the part that has never been tested.
    Bytes, not text — the byte-exactness claims cannot be checked through a
    decoder that normalises newlines.
    """
    done = subprocess.run(
        [sys.executable, CLI, *args], input=stdin, capture_output=True, check=False
    )
    return done.returncode, done.stdout, done.stderr


# A payload of our own, not data/samples/. The samples are gitignored real API
# responses, so a fresh checkout has none — property_test.py is currently the
# only gate that survives that, and this one should too.
#
# repeated() rather than a hand-written pair of rows, per Rule 6's corollary:
# two rows lose to plain JSON on MIN_TABLE_SAVING, so a small payload silently
# tests the fallback instead of the table. It builds eight for that reason.
#
# COMPACT separators, both of them, and that is not a detail. Written with
# json.dumps' defaults, the "unchanged" fixture below compressed by 24.8% —
# entirely by removing the `", "` and `": "` padding json.dumps had just added
# and no real file contains. It looked like a passing 0% test failing, when it
# was a fixture that was never 0% in the first place. Rule 5, in miniature:
# measure against the bytes a file would actually hold.
COMPACT = (",", ":")

TABULATES = json.dumps(repeated(["kept", "together"], ["and", "apart"], rows=40),
                       separators=COMPACT)

# Cells long enough to clear MIN_STORE_SAVING, so `--store` actually stashes
# something. Generated here rather than read from data/samples/, which is
# gitignored and absent from the tree pre-commit builds — the same reason every
# other fixture in this file is generated.
BULKY = json.dumps(
    [{"id": index, "body": f"paragraph {index} " + "prose about the thing " * 12}
     for index in range(12)],
    separators=COMPACT)

# Compresses to nothing: no record array anywhere, so no table is possible, and
# compress_json hands the input straight back. This is Open-Meteo's shape in
# miniature and it is the case a do-nothing CLI passes by accident.
UNCHANGED = json.dumps({"hourly": {"time": list(range(50)), "temp": [1.5] * 50}},
                       separators=COMPACT)

PIPE_BUFFER = 65536
# macOS and Linux both default to a 64KB pipe. A document smaller than this is
# written in full before the reader can close the pipe underneath it.

# For the closed-pipe checks only, and the size is the whole point. TABULATES
# compresses to 665 bytes, which fits entirely in the pipe buffer — the writer
# finishes and exits 0 before `head -1` ever closes the pipe, so SIGPIPE never
# fires and the checks named for it passed with the handler deleted.
#
# Deliberately incompressible, and that is a lesson rather than a detail. The
# first version was `repeated(...)` at 8,000 rows, which rendered to ~139KB and
# worked — until #dict landed, collapsed its two-distinct-value column to
# indices, and dropped the document back under the buffer. Both pipe checks
# failed on the spot, which is the gate working: a fixture that quietly stops
# reaching its subject is the exact failure Rule 13 exists for, and here a
# compression improvement was enough to cause it. Unique per-row strings cannot
# be factored out by any rule this compressor has, so the document stays large
# whatever gets added next.
PIPE_FILLING = json.dumps(
    [{"i": i, "blob": f"row-{i}-" + "abcdefghij"[i % 10] * 60} for i in range(1500)],
    separators=COMPACT,
)


def nested(levels):
    """`[[[...]]]` — the shape that blows a recursion limit."""
    return b"[" * levels + b"]" * levels


# TWO pathological payloads, because there are two recursions in the pipeline
# and the first one to blow stops the other from ever running. json.loads copes
# with roughly 3x the recursion limit before its C scanner gives up, so:
PARSES_THEN_CRASHES = nested(sys.getrecursionlimit() * 3)  # dies in compress_json
CRASHES_IN_PARSER = nested(sys.getrecursionlimit() * 10)   # dies in detect_content_type
#
# One number covered one half. At 3,000 the guard around compress_json was
# exercised and the parse was not, which is how a RecursionError from json.loads
# shipped; raising it to 10,000 to catch that swapped which half was tested,
# and moving compress_json back outside the guard still passed every check.


def main():
    print("CLI contract\n")

    # ---- the tool actually ran -------------------------------------------
    # First, because every check after it is meaningless if the compressor was
    # never reached. A `cat` passes the round-trip; it cannot produce a header.
    code, out, err = run([], stdin=TABULATES.encode())
    tabulated = out.startswith(render.FORMAT_MARKER.encode())
    check("stdin: a tabulable payload comes back as a #margin/v1 document",
          code == 0 and tabulated, f"exit={code} first 40 bytes={out[:40]!r}")

    if not tabulated:
        # Rule 2: a sweep that can silently test nothing must say so. Without
        # this, a change that made every payload fall back to JSON would leave
        # every remaining check passing on the fallback.
        print("\n  !! the generated payload did not tabulate — the checks below")
        print("     would be testing the JSON fallback, not the format.")

    # ---- stdout carries the document and nothing else ---------------------
    # Aimed at the claim, not the data. stderr is thrown away here on purpose:
    # if a note leaked into stdout this parse fails, and if the document leaked
    # into stderr this one comes up short.
    try:
        restored = decompress(out.decode())
        clean = same_json(restored, strip_boilerplate(json.loads(TABULATES)))
    except Exception as exc:
        restored, clean = None, False
        print(f"       (stdout did not decompress: {type(exc).__name__}: {exc})")
    # same_json, never ==: Python says True == 1 and 0 == 0.0, so == reports
    # success on a document that decoded ints as floats (Rule 9).
    check("stdout alone round-trips — no note leaked into it", clean)

    check("notes go to stderr, and say something",
          b"margin:" in err and b"tokens" in err, f"stderr={err[:120]!r}")
    check("the document never appears on stderr",
          render.FORMAT_MARKER.encode() not in err)

    # ---- a path and stdin are one input path ------------------------------
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(TABULATES)
        tabulates_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(UNCHANGED)
        unchanged_path = f.name
    try:
        _, from_path, _ = run([tabulates_path])
        _, from_dash, _ = run(["-"], stdin=TABULATES.encode())
        # The whole pipe rests on this one: `curl | margin` and `margin f.json`
        # must not be two different programs.
        check("`margin f.json` and `cat f.json | margin` agree byte for byte",
              from_path == out and from_dash == out,
              f"path={len(from_path)}B stdin={len(out)}B dash={len(from_dash)}B")

        # ---- unchanged means byte-for-byte unchanged ----------------------
        # Two assertions, deliberately. "Correctly declined to compress" and
        # "the tool never ran" produce identical stdout, so stdout alone cannot
        # tell them apart — the stderr line is what distinguishes them, and it
        # has to be checked separately or the pair proves nothing.
        raw = UNCHANGED.encode()
        code, out2, err2 = run([unchanged_path])
        check("0% payload: stdout is byte-identical to the input",
              code == 0 and out2 == raw,
              f"exit={code} in={len(raw)}B out={len(out2)}B")
        check("0% payload: stderr says it was returned unchanged",
              b"unchanged" in err2, f"stderr={err2!r}")

        # ---- a missing path is a typo, not a crash ------------------------
        code, out3, err3 = run([os.path.join(SRC, "definitely-not-here.json")])
        check("missing file: exit 2, one line, no traceback",
              code == 2 and out3 == b"" and b"Traceback" not in err3
              and err3.count(b"\n") == 1, f"exit={code} stderr={err3!r}")
    finally:
        os.unlink(tabulates_path)
        os.unlink(unchanged_path)

    # ---- not JSON is not an error -----------------------------------------
    html = b"<html><body>502 Bad Gateway</body></html>"
    code, out4, err4 = run([], stdin=html)
    check("not JSON: exit 0 and byte-identical passthrough",
          code == 0 and out4 == html, f"exit={code} out={out4!r}")
    # The exit code is 0 by design, so this stderr line is the ONLY thing
    # standing between a failed curl and an HTML error page on the clipboard.
    check("not JSON: stderr says so",
          b"not JSON" in err4, f"stderr={err4!r}")

    # Invalid UTF-8 is not JSON either, and must survive rather than crash on
    # the decode. \xff is not a legal byte anywhere in UTF-8.
    code, out5, _ = run([], stdin=b"\xff\xfe not text at all")
    check("invalid UTF-8: exit 0 and byte-identical passthrough",
          code == 0 and out5 == b"\xff\xfe not text at all", f"exit={code}")

    # ---- nothing to read is an error ---------------------------------------
    code, out6, err6 = run([], stdin=b"")
    check("empty input: exit 2, nothing on stdout",
          code == 2 and out6 == b"", f"exit={code} stdout={out6!r}")

    code, _, _ = run(["a.json", "b.json"])
    check("two paths: exit 2", code == 2, f"exit={code}")

    code, _, _ = run(["--verbose"], stdin=TABULATES.encode())
    check("unknown option: exit 2 rather than a file named --verbose",
          code == 2, f"exit={code}")

    code, out7, _ = run(["--help"])
    check("--help: exit 0, usage on stdout",
          code == 0 and b"usage:" in out7, f"exit={code} stdout={out7!r}")

    code, _, err8 = run(["-q"], stdin=UNCHANGED.encode())
    check("-q: stderr completely silent", code == 0 and err8 == b"", f"stderr={err8!r}")

    # ---- piping into something that stops reading --------------------------
    # `margin f.json | head -1` is how you look at a document, and it used to
    # print a BrokenPipeError traceback under the output. A traceback on stderr
    # is not cosmetic here: it is the stream the notes live on.
    def through_head(argv):
        """Run argv, feed it PIPE_FILLING, and let `head -1` walk away.

        Returns (exit code, first line the reader got, stderr).
        """
        reader = subprocess.Popen(["head", "-1"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)
        writer = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=reader.stdin,
                                  stderr=subprocess.PIPE)
        reader.stdin.close()
        try:
            writer.stdin.write(PIPE_FILLING.encode())
            writer.stdin.close()
        except BrokenPipeError:
            pass  # margin can die before it has read all of its own input
        err = writer.stderr.read()
        first_line = reader.stdout.read()
        writer.wait()
        reader.wait()
        return writer.returncode, first_line, err

    # Rule 2, and the reason these two checks were worthless once already: if
    # the document fits the pipe buffer, the pipe never closes under the writer
    # and both checks below pass without provoking the signal they are named
    # for. Assert the premise rather than trusting a row count to keep holding.
    pipe_doc, _ = compress_json(json.loads(PIPE_FILLING), PIPE_FILLING)
    check(f"the pipe fixture still exceeds the {PIPE_BUFFER}-byte pipe buffer",
          len(pipe_doc.encode()) > PIPE_BUFFER,
          f"document is {len(pipe_doc.encode())}B — the two checks below prove nothing")

    code, first_line, pipe_err = through_head([sys.executable, CLI])
    # Assert the SIGNAL, not just the absence of a traceback. "No traceback" is
    # also true of a run where the pipe was never closed under the writer, which
    # is exactly what happened while this check used a 665-byte document: it fit
    # the buffer, margin finished and exited 0, and the check passed with
    # die_on_broken_pipe() gutted to `pass`. Dying on SIGPIPE is the behaviour,
    # so the exit status is the thing to check.
    check("closed pipe (| head -1): dies on SIGPIPE, no traceback",
          code == -signal.SIGPIPE and b"Traceback" not in pipe_err
          and b"BrokenPipe" not in pipe_err,
          f"exit={code} (want {-signal.SIGPIPE}) stderr={pipe_err[-200:]!r}")

    # The same thing through compress.py, which is a second entry point and so
    # a second place to forget a line of setup. It did: the first version of
    # its __main__ called cli.main() without die_on_broken_pipe(), so the exact
    # traceback above was still live here while the check passed on cli.py.
    # And it must still produce a document. Checking only "no traceback" passed
    # on a compress.py with its entire __main__ block deleted — a silent no-op
    # exiting 0 with empty stderr has no traceback either. Caught by the second
    # review, and it is the third instance of the same mistake in this file:
    # an assertion aimed near the claim rather than at it (Rules 3, 12).
    # So: capture what reached the reader and demand the format marker.
    compress_py = os.path.join(SRC, "compress.py")
    legacy_code, legacy_out, legacy_err = through_head([sys.executable, compress_py])
    # Three conditions, and each one caught a different real defect: the marker
    # caught a compress.py whose __main__ had been deleted entirely (a silent
    # no-op has no traceback either), the traceback condition is the original
    # bug, and the signal is what makes the pair mean anything at all.
    check("compress.py entry point: document reaches the reader, dies on SIGPIPE",
          legacy_out.startswith(render.FORMAT_MARKER.encode())
          and legacy_code == -signal.SIGPIPE
          and b"Traceback" not in legacy_err and b"BrokenPipe" not in legacy_err,
          f"exit={legacy_code} stdout={legacy_out[:40]!r} stderr={legacy_err[-200:]!r}")

    # ---- an unexpected crash must not empty the pipe ------------------------
    # json.loads parses this in its C scanner, then strip_boilerplate recurses
    # through it and blows the stack. RecursionError is not a JSONDecodeError,
    # so it escaped every guard: exit 1, traceback, and zero bytes on stdout —
    # `curl ... | margin | pbcopy` clearing the clipboard. The contract is that
    # bytes in means bytes out, whatever goes wrong in between.
    # Depth derived from the recursion limit, not the hardcoded 3,000 this
    # started as. 3,000 only crashes because it lands in the narrow band where
    # json.loads' C scanner copes and strip_boilerplate's recursion does not —
    # on an interpreter where 3,000 survives both, the payload passes through
    # legitimately and the check would report a failure against correct
    # behaviour. Ten times the limit is past every recursion in the pipeline.
    #
    # It also covers more than it used to: at this depth json.loads itself
    # raises, from inside detect_content_type, which is where the first version
    # of the guard was not looking.
    for label, payload, should_parse in (
        ("dies in compression", PARSES_THEN_CRASHES, True),
        ("dies in the parser", CRASHES_IN_PARSER, False),
    ):
        # Rule 2: a check that can silently stop exercising its code path must
        # report its own coverage. Whether a payload reaches compress_json
        # depends on where json.loads gives up, which is an interpreter detail,
        # not something this file can assert by choosing a number.
        try:
            json.loads(payload.decode())
            parses = True
        except RecursionError:
            parses = False
        if parses != should_parse:
            print(f"\n  !! '{label}' payload no longer exercises that half —")
            print(f"     json.loads {'succeeds' if parses else 'fails'} on it here.")

        code, out8, err9 = run([], stdin=payload)
        check(f"pathological input ({label}): exit 0, bytes back, stderr says what broke",
              code == 0 and out8 == payload and b"passed through unchanged" in err9,
              f"exit={code} out={len(out8)}B of {len(payload)}B stderr={err9[-120:]!r}")

    # ---- the wrapper -------------------------------------------------------
    # bin/margin holds real logic — symlink resolution and the venv path — and
    # is the only file here that is not Python.
    #
    # The no-venv branch is not a skip, and that is deliberate. pre-commit runs
    # this file out of a scratch checkout of the index, which by construction
    # has no .venv, so a skip would mean the launcher is gated by the Claude
    # hook alone and never at commit time. A missing venv has a defined correct
    # behaviour — exit 2 saying which venv and how to make it — so check that
    # instead. Same branch a fresh clone takes, now covered rather than waved
    # through.
    # realpath, not abspath. bin/margin resolves its repo with `cd -P`, which
    # resolves symlinks, so on macOS it reports /private/var/... where abspath
    # gives /var/... — and the assertion below passed only because one is a
    # substring of the other. A check that holds by coincidence of this
    # platform's naming is not a check.
    repo = os.path.realpath(os.path.dirname(SRC))
    venv_python = os.path.join(repo, ".venv", "bin", "python")
    if not os.path.exists(WRAPPER):
        check("bin/margin exists", False, f"not at {WRAPPER}")
    elif not os.access(venv_python, os.X_OK):
        # Checking exit 2 and "uv venv" here was not enough, and the review
        # caught it: neither depends on the symlink loop, so deleting that loop
        # outright left this printing "all CLI checks pass" — in the one branch
        # pre-commit can ever reach. Rule 12 written, then violated in the fix
        # for Rule 12.
        #
        # A wrapper that fails to resolve its own symlink computes the wrong
        # repo, so it names the wrong .venv. Invoking through a link from an
        # unrelated directory and demanding the message name THIS repo's venv
        # exercises the resolution without needing a venv to exist — the error
        # path carries the evidence.
        with tempfile.TemporaryDirectory() as elsewhere:
            link = os.path.join(elsewhere, "margin")
            os.symlink(WRAPPER, link)
            done = subprocess.run([link], input=TABULATES.encode(), cwd=elsewhere,
                                  capture_output=True, check=False)
        check("bin/margin without a .venv: exit 2, and it resolved the symlink "
              "to name the right repo",
              done.returncode == 2 and b"uv venv" in done.stderr
              and os.path.join(repo, ".venv").encode() in done.stderr,
              f"exit={done.returncode} stderr={done.stderr!r}")
    else:
        done = subprocess.run([WRAPPER], input=TABULATES.encode(),
                              capture_output=True, check=False)
        check("bin/margin: finds the repo from its own path and compresses",
              done.returncode == 0 and done.stdout == out,
              f"exit={done.returncode} stderr={done.stderr[-200:]!r}")

        # Through a symlink, from an unrelated directory — which is the only
        # way it is ever actually invoked once installed in ~/.local/bin. The
        # dirname of the symlink says nothing about where the repo is, so this
        # is what the resolution loop exists for.
        with tempfile.TemporaryDirectory() as elsewhere:
            link = os.path.join(elsewhere, "margin")
            os.symlink(WRAPPER, link)
            done = subprocess.run([link], input=TABULATES.encode(), cwd=elsewhere,
                                  capture_output=True, check=False)
            check("bin/margin: works through a symlink from another directory",
                  done.returncode == 0 and done.stdout == out,
                  f"exit={done.returncode} stderr={done.stderr[-200:]!r}")

    # --- the store subcommands ------------------------------------------
    #
    # As subprocesses, like everything else here (Rule 11): argv, exit codes and
    # a real store on disk only exist at the process boundary, and these four
    # commands are nothing BUT argv, exit codes and a store on disk.
    #
    # Each runs against its own $MARGIN_STORE in a temp directory. A test that
    # swept ~/.margin would be a test that deletes the user's data the first
    # time it is wrong.
    with tempfile.TemporaryDirectory() as store_root, \
            tempfile.TemporaryDirectory() as other_root:
        env = dict(os.environ, MARGIN_STORE=store_root)
        done = subprocess.run([sys.executable, CLI, "--store", "-"],
                              input=BULKY.encode(), env=env,
                              capture_output=True, check=False)
        document = done.stdout.decode()

        # Rule 1, first: if the fixture did not actually store anything, every
        # check below passes against an empty store and proves nothing.
        stored_line = [l for l in document.split("\n") if l.startswith("#store")]
        objects_dir = os.path.join(store_root, "objects")
        n_objects = len(os.listdir(objects_dir)) if os.path.isdir(objects_dir) else 0
        check("fixture: --store really wrote objects and a #store line",
              bool(stored_line) and n_objects > 0,
              f"#store={stored_line} objects={n_objects}")
        if not stored_line or not n_objects:
            print("  (the store subcommand checks below would be testing an "
                  "empty store — skipping them would hide that, so they run "
                  "and will fail)")

        doc_id = stored_line[0][len("#store"):].strip().strip('"') if stored_line else ""

        done = subprocess.run([sys.executable, CLI, "fsck"], env=env,
                              capture_output=True, check=False)
        check("fsck: a healthy store exits 0 and says clean",
              done.returncode == 0 and b"clean" in done.stdout,
              f"exit={done.returncode} out={done.stdout[-120:]!r}")

        # The quoted form is what a document actually shows, so it is what a
        # caller copies. mcp_server learned this one the same way.
        bundle_path = os.path.join(other_root, "bundle.json")
        done = subprocess.run([sys.executable, CLI, "export",
                               f'"{doc_id}"', bundle_path],
                              env=env, capture_output=True, check=False)
        check("export: accepts the doc id with the quotes the #store line shows",
              done.returncode == 0 and os.path.exists(bundle_path),
              f"exit={done.returncode} stderr={done.stderr[-160:]!r}")

        check("export: the bundle carries the objects, not just the index",
              os.path.exists(bundle_path)
              and len(json.loads(open(bundle_path, encoding="utf-8")
                                 .read())["objects"]) == n_objects,
              "bundle objects != store objects")

        # The whole point of the feature: another store, which has never seen
        # this payload, resolves the document.
        second = dict(os.environ, MARGIN_STORE=os.path.join(other_root, "store"))
        done = subprocess.run([sys.executable, CLI, "import", bundle_path],
                              env=second, capture_output=True, check=False)
        check("import: a fresh store takes the bundle",
              done.returncode == 0 and doc_id.encode() in done.stdout,
              f"exit={done.returncode} stderr={done.stderr[-160:]!r}")

        done = subprocess.run([sys.executable, CLI, "fsck"], env=second,
                              capture_output=True, check=False)
        check("import: and the imported store is clean",
              done.returncode == 0 and b"clean" in done.stdout,
              f"exit={done.returncode} out={done.stdout[-120:]!r}")

        # gc, and the reason it is dry by default: this is the only command in
        # the project that destroys anything.
        os.remove(os.path.join(store_root, "docs", doc_id + ".json"))
        done = subprocess.run([sys.executable, CLI, "gc"], env=env,
                              capture_output=True, check=False)
        after_dry = len(os.listdir(objects_dir))
        check("gc: without --delete it removes nothing and says so",
              done.returncode == 0 and b"nothing was deleted" in done.stdout
              and after_dry == n_objects,
              f"exit={done.returncode} objects {n_objects}->{after_dry}")

        done = subprocess.run([sys.executable, CLI, "gc", "--delete"], env=env,
                              capture_output=True, check=False)
        check("gc --delete: sweeps exactly the unreferenced objects",
              done.returncode == 0 and len(os.listdir(objects_dir)) == 0,
              f"exit={done.returncode} left={len(os.listdir(objects_dir))}")

        done = subprocess.run([sys.executable, CLI, "export", "nosuchdoc",
                               os.path.join(other_root, "x.json")],
                              env=env, capture_output=True, check=False)
        check("export: an unknown document fails loudly, with no traceback",
              done.returncode == 1 and b"Traceback" not in done.stderr
              and done.stderr.strip().startswith(b"margin:"),
              f"exit={done.returncode} stderr={done.stderr[-160:]!r}")

        done = subprocess.run([sys.executable, CLI, "fsck", "extra"], env=env,
                              capture_output=True, check=False)
        check("a subcommand called wrong exits 2, like the rest of the CLI",
              done.returncode == 2, f"exit={done.returncode}")

    print()
    if failures:
        print(f"{len(failures)} CLI check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("all CLI checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
