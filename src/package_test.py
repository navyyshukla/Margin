"""Does the thing a stranger installs actually work?

Every other gate here runs inside the repo, against src/ and margin/, with a
.venv built by hand. Every one of them can pass while the shipped artifact is
broken, because these failures only exist after a build:

  - margin/web/index.html missing from the wheel -> `margin web` serves a 404
  - a bare-name import survived the package move -> ModuleNotFoundError: table
  - [project.scripts] misdeclared            -> margin: command not found
  - google-genai left in the runtime deps    -> a Gemini SDK to compress JSON

That is the same class as the failure docs/cli.md records twice — four green
gates that had never seen an argument, a stream or an exit code (Rule 11) —
one layer further out. So this builds a real wheel, installs it into a venv
with no path back into this repo, and drives the installed command.

Rule 15: test the implementation that ships. An in-repo import is not it.

No sample payload — the payloads are inline — so this runs in CI on every
push, which means it runs on Linux while this is developed on macOS. That is
where the platform assumptions get caught.

Run: python src/package_test.py
"""

import json
import os
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Three records, enough to clear MIN_ROWS_TO_TABULATE, with a constant column so
# the document exercises #const rather than only the header. The constant column
# is not decoration: without it three records of two short keys save 3.8%, below
# MIN_TABLE_SAVING, and `margin` correctly returns the input unchanged — which
# would make the check below fail for a reason that is not a packaging bug.
# Measured 2026-10-12: this payload saves 17.9%.
#
# Inline because data/samples/ is gitignored and this gate has to run in CI.
PAYLOAD = json.dumps([
    {"id": 1, "state": "open", "title": "first"},
    {"id": 2, "state": "open", "title": "second"},
    {"id": 3, "state": "open", "title": "third"},
])

failures = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(name)
    return ok


def build_wheel(into):
    """Build a real wheel. Returns its path, or raises with the build output."""
    done = subprocess.run(["uv", "build", "--wheel", "--out-dir", into],
                          cwd=REPO, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"uv build failed:\n{done.stdout}\n{done.stderr}")
    wheels = [f for f in os.listdir(into) if f.endswith(".whl")]
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, got {wheels}")
    return os.path.join(into, wheels[0])


def install(wheel, venv):
    """A venv with the wheel in it and NOTHING else.

    No --system-site-packages, no requirements.txt, no path back into REPO: if
    the wheel does not carry a file, the installed command will not find it.
    That is the entire question this gate asks.
    """
    subprocess.run(["uv", "venv", venv, "--python", "3.12"],
                   capture_output=True, text=True, check=True)
    done = subprocess.run(["uv", "pip", "install", "--python", venv, wheel],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"install failed:\n{done.stdout}\n{done.stderr}")
    return os.path.join(venv, "bin", "margin")


def run(margin, args, stdin=None, cwd=None):
    """The installed command, from a directory that is not the repo.

    cwd defaults to the venv's own bin directory on purpose. `margin` has to
    work from anywhere, which is the one property bin/margin's four retired
    cli_test checks were testing — see the comment where they used to be.
    """
    done = subprocess.run([margin, *args],
                          input=stdin.encode() if stdin else None,
                          capture_output=True, cwd=cwd or os.path.dirname(margin),
                          check=False)
    return done.returncode, done.stdout, done.stderr


def main():
    print("packaged install\n")
    with tempfile.TemporaryDirectory() as tmp:
        dist = os.path.join(tmp, "dist")
        os.makedirs(dist)
        try:
            wheel = build_wheel(dist)
        except RuntimeError as exc:
            check("the wheel builds", False, str(exc))
            return 1
        check("the wheel builds", True)

        # The one check that needs no install: is the HTML actually in there?
        # A wheel is a zip, and asking it directly names the missing file
        # instead of leaving `margin web` to serve a 404 later.
        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        held = len([n for n in names if n.startswith("margin/")])
        ui_exists = os.path.exists(os.path.join(REPO, "margin", "web", "index.html"))
        if ui_exists:
            check("margin/web/index.html is in the wheel",
                  "margin/web/index.html" in names,
                  f"wheel holds {held} margin/ files")
        else:
            # Conditional ONLY until Task 8 of
            # docs/superpowers/plans/2026-10-12-shipping-and-local-ui.md lands.
            # Delete this branch with that task — a check that cannot fail is
            # not a check (Rule 14), and this one is the first failure this
            # gate exists to catch.
            print("  ----  margin/web/index.html: not written yet (Task 8)")

        try:
            margin = install(wheel, os.path.join(tmp, "venv"))
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            check("the wheel installs into a clean venv", False, str(exc))
            return 1
        check("the wheel installs into a clean venv", True)
        check("the console script exists", os.path.exists(margin), margin)

        code, out, err = run(margin, ["--help"])
        check("margin --help exits 0 from outside the repo",
              code == 0 and b"usage: margin" in out,
              f"exit {code}, stderr={err[:200]!r}")

        code, out, err = run(margin, ["-"], stdin=PAYLOAD)
        check("the installed command compresses",
              code == 0 and out.startswith(b"#margin/v1"),
              f"exit {code}, stdout={out[:120]!r}, stderr={err[:200]!r}")

        # Rule 1's shape: a document is not proof the pipeline ran unless it
        # comes back. Round-trip through the INSTALLED package, not this repo's
        # — the import is the thing under test here as much as the document is.
        py = os.path.join(os.path.dirname(margin), "python")
        done = subprocess.run(
            [py, "-c",
             "import sys, json\n"
             "from margin.decompress import decompress\n"
             "from margin.table import same_json\n"
             "print(same_json(decompress(sys.stdin.read()), json.loads(sys.argv[1])))",
             PAYLOAD],
            input=out.decode(), capture_output=True, text=True, check=False,
            cwd=os.path.dirname(margin))
        check("the document round-trips inside the installed package",
              done.stdout.strip() == "True",
              f"stdout={done.stdout!r} stderr={done.stderr[-300:]!r}")

        # google-genai must not have come along, and `uv pip list` in that venv
        # is the only honest way to ask — pyproject.toml saying so is the claim,
        # not the evidence (Rule 3).
        done = subprocess.run(["uv", "pip", "list", "--python",
                               os.path.join(tmp, "venv"), "--format", "json"],
                              capture_output=True, text=True, check=False)
        installed = {p["name"].lower() for p in json.loads(done.stdout or "[]")}
        check("google-genai did not ship", "google-genai" not in installed,
              f"installed: {sorted(installed)}")
        check("tiktoken and mcp did ship", {"tiktoken", "mcp"} <= installed,
              f"installed: {sorted(installed)}")

        # JSON-RPC over a pipe, against the INSTALLED server. src/mcp_test.py
        # already drives the protocol in depth; what this asks is narrower and
        # only answerable here — that `margin mcp` is reachable at all once the
        # code lives inside a tool venv whose path nobody can predict. Before
        # packaging, a client was pointed at a path in this repo; there is no
        # such path now.
        #
        # 2025-06-18 is the version src/mcp_test.py speaks; keeping one number
        # in two files would be two numbers eventually.
        hello = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "package_test", "version": "0"}},
        }) + "\n"
        done = subprocess.run([margin, "mcp"], input=hello.encode(),
                              capture_output=True, timeout=60,
                              cwd=os.path.dirname(margin), check=False)
        check("margin mcp answers an initialize over stdio",
              b'"result"' in done.stdout,
              f"stdout={done.stdout[:200]!r} stderr={done.stderr[:300]!r}")

        # 0 or 1 both mean it ran: fsck exits 1 when content a document needs is
        # gone, and a store that does not exist yet is not a failure of fsck.
        # What this rules out is 2 (called wrong) and a traceback.
        code, out, err = run(margin, ["fsck"])
        check("margin fsck runs from the installed command",
              code in (0, 1) and b"Traceback" not in err,
              f"exit {code}, stderr={err[:200]!r}")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all packaged-install checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
