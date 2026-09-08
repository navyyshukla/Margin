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
import subprocess
import sys
import tempfile

import render
from compress import strip_boilerplate
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

# Compresses to nothing: no record array anywhere, so no table is possible, and
# compress_json hands the input straight back. This is Open-Meteo's shape in
# miniature and it is the case a do-nothing CLI passes by accident.
UNCHANGED = json.dumps({"hourly": {"time": list(range(50)), "temp": [1.5] * 50}},
                       separators=COMPACT)


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
    reader = subprocess.Popen(["head", "-1"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    writer = subprocess.Popen([sys.executable, CLI], stdin=subprocess.PIPE,
                              stdout=reader.stdin, stderr=subprocess.PIPE)
    reader.stdin.close()
    writer.stdin.write(TABULATES.encode())
    writer.stdin.close()
    pipe_err = writer.stderr.read()
    writer.wait()
    reader.wait()
    check("closed pipe (| head -1): no traceback on stderr",
          b"Traceback" not in pipe_err and b"BrokenPipe" not in pipe_err,
          f"stderr={pipe_err[-200:]!r}")

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
    venv_python = os.path.join(os.path.dirname(SRC), ".venv", "bin", "python")
    if not os.path.exists(WRAPPER):
        check("bin/margin exists", False, f"not at {WRAPPER}")
    elif not os.access(venv_python, os.X_OK):
        done = subprocess.run([WRAPPER], input=TABULATES.encode(),
                              capture_output=True, check=False)
        check("bin/margin without a .venv: exit 2 and says how to make one",
              done.returncode == 2 and b"uv venv" in done.stderr,
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
