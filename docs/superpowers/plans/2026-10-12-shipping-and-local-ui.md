# Shipping Margin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pip install margin` work, and give it a local page at `margin web` that shows what a payload saved.

**Architecture:** The ten product files move from flat `src/` into a real package `margin/` with explicit relative imports and a `[project.scripts]` console entry; the seventeen harness files stay in `src/` and import the product by package name. Two new product files add a stdlib `http.server` UI that calls the same `compress_json` the CLI calls. One new gate builds the wheel, installs it into a throwaway venv, and drives the installed command.

**Tech Stack:** Python 3.12, hatchling, `http.server` (stdlib), `tiktoken==0.14.0`, `mcp==2.2.0`. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-12-shipping-and-local-ui-design.md`

## Global Constraints

- **Python 3.12.** What the project is developed and measured on; `gates.yml` pins it.
- **No new runtime dependency.** `pip install margin` installs `tiktoken==0.14.0` and `mcp==2.2.0` and nothing else. `google-genai==2.22.0` moves to an optional `[dev]` extra.
- **Dependencies stay pinned exactly.** A floating `tiktoken` moves every token count in `docs/`.
- **Import package and command are both `margin`.** Distribution name `margin`, fallback `margin-json` only if PyPI refuses it.
- **Internal imports inside `margin/` are explicit relative** (`from .table import MISSING`), never bare-name.
- **The UI binds `127.0.0.1` only.** No `--host` flag, ever.
- **One compressor.** `web.py` calls `compress_json`; it never reimplements or subprocesses it.
- **Every threshold or magic number carries a one-line comment saying where it came from**, and says so honestly when it is arbitrary rather than measured (`.claude/rules/src-python.md`).
- **Use `table.same_json`, never `==`, to compare JSON.**
- **Rule numbers in comments refer to `docs/harness.md`. Do not renumber; new rules append.**
- **Mutation arithmetic: 31 − 1 + 4 = 34.** The spec says 35; it did not account for `bin/margin`'s mutation retiring with the file. 34 is the number to write in the docs.

## Review Focus

Five things the spec implies, that no task's main deliverable exercises, that would bite a real user. Each has a test added to the task that owns the code.

1. **Port 8731 is already taken** (a second `margin web`, or anything else) — must fall back to an ephemeral port and print the real URL, not traceback. → Task 7.
2. **Two requests at once** — `ThreadingHTTPServer` plus `token_count`'s memo means concurrent `/compress` calls share mutable state; both must return correct counts. → Task 7.
3. **A paste far larger than any sample** — an unbounded `Content-Length` read is an OOM a browser can trigger; needs a cap and a 413. → Task 7.
4. **Bytes that are not UTF-8 POSTed to `/compress`** — must hand them back unchanged with a note, matching the CLI's exit-0 passthrough, not 500. → Task 7.
5. **A request with no `Content-Length`, or a `HEAD`/`OPTIONS` from a browser or probe** — must be a clean 400/404, never a traceback into the terminal. → Task 7.

---

### Task 1: Move the product into a package, atomically

Everything that names a path must move in one commit. `pre-commit` greps staged files for `^(src/.*\.py|bin/margin)$`, so this commit still triggers the gates (harness files are staged); the *next* commit touching only `margin/*.py` would not, which is why the hooks are updated here rather than later.

**Files:**
- Create: `margin/__init__.py`
- Move: `src/{cli,compress,decompress,detect,render,store,store_commands,table,tokens,mcp_server}.py` → `margin/`
- Delete: `bin/margin`
- Modify: all ten moved files (imports), `src/cli_test.py`, `src/mcp_test.py`, `src/mutation_test.py`, `src/property_test.py`, `src/eval_harness.py`, `src/eval_model.py`, `src/checks_*.py` (8), `src/measure_*.py` (3), `.claude/hooks/run_eval.sh`, `.githooks/pre-commit`, `.github/workflows/gates.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: the package `margin` with `margin.cli.main(argv=None) -> int`, `margin.compress.compress_json(data, original_text=None, store=None) -> (str, list[str])`, `margin.mcp_server.main() -> None`. Harness files import these as `from margin.compress import compress_json`.

- [ ] **Step 1: Move the ten files and add the package marker**

```bash
cd "/Users/navyshukla/Margin Project"
mkdir -p margin
git mv src/cli.py src/compress.py src/decompress.py src/detect.py src/render.py \
       src/store.py src/store_commands.py src/table.py src/tokens.py \
       src/mcp_server.py margin/
git rm bin/margin
```

Then create `margin/__init__.py`:

```python
"""margin — compress a JSON API response on its way into an LLM prompt.

A package, as of 2026-10-12, and `bin/margin` is gone. That wrapper's own
comment declined packaging because src/ was flat modules importing each other
by bare name, so a console script would have meant either restructuring every
file the harness guards or installing `table` and `render` into site-packages
under names anything could collide with.

That argument held while nothing was packaged. Once `pip install margin` is the
goal it inverts: the restructuring is no longer a cost paid for nothing, it is
the thing being bought. The seventeen harness files did NOT move — they import
this package by name — so the rename touched ten files, not twenty-seven.

Nothing is exported here on purpose. The entry point is margin.cli:main.
"""
```

- [ ] **Step 2: Rewrite the internal imports in all ten moved files**

Every bare-name import of a sibling becomes explicit relative. In `margin/cli.py:44-48`:

```python
from . import render
from . import store as store_module
from . import store_commands
from .compress import compress_json, token_count
from .detect import detect_content_type
```

In `margin/compress.py:24-28`:

```python
from . import render
from . import store as store_module
from .decompress import decompress
from .tokens import token_count
from .table import MIN_ROWS_TO_TABULATE, build_table, find_record_array, same_json
```

Apply the same treatment to `decompress.py`, `detect.py`, `render.py`, `store.py`, `store_commands.py`, `table.py`, `tokens.py`, `mcp_server.py`. Find every one:

```bash
grep -nE '^(import|from) (render|store|store_commands|compress|decompress|detect|table|tokens|mcp_server|cli)\b' margin/*.py
```

That grep must return nothing when the step is done.

- [ ] **Step 3: Fix `margin/compress.py`'s `__main__` block**

`runpy.run_path` on a file inside a package re-executes it outside the package, so its relative imports fail. The block at the end of the file becomes:

```python
if __name__ == "__main__":
    # `python -m margin.compress` stays a working entry point, and it goes
    # through cli.main() rather than reimplementing it — a second entry point
    # is a second place to forget die_on_broken_pipe(), which is the exact bug
    # docs/cli.md records. runpy.run_path is gone: run_path on a module inside
    # a package executes it with no package context, so every relative import
    # in it fails.
    import sys

    from .cli import die_on_broken_pipe, main

    die_on_broken_pipe()
    sys.exit(main())
```

- [ ] **Step 4: Point the harness at the package**

Every harness file that imported the product by bare name now imports it from `margin`. Find them:

```bash
grep -nE '^(import|from) (render|store|compress|decompress|detect|table|tokens|cli|mcp_server|store_commands)\b' src/*.py
```

Each becomes the `margin.`-prefixed form, e.g. in `src/property_test.py`:

```python
from margin.compress import compress_json
from margin.decompress import decompress
from margin.table import same_json
```

- [ ] **Step 5: Repoint `src/cli_test.py` at the module, and delete the wrapper checks**

`src/cli_test.py:48-50` becomes:

```python
SRC = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SRC)
# `python -m margin.cli`, not a path to cli.py. Running the file directly would
# execute it outside its package and every relative import in it would fail.
# REPO on PYTHONPATH so the subprocess imports the tree this gate was copied
# into — not whatever `margin` happens to be installed on the machine. That
# distinction is the whole reason this gate finds cli.py relative to its own
# __file__ (see .githooks/pre-commit).
CLI = ["-m", "margin.cli"]
```

and every `[sys.executable, CLI, *args]` becomes `[sys.executable, *CLI, *args]` with the environment carrying `PYTHONPATH=REPO`. Add one helper near `run()` and use it everywhere a subprocess is spawned in this file:

```python
def _env(extra=None):
    """PYTHONPATH pinned to this gate's own tree, plus anything a check adds.

    Without this the subprocess imports an INSTALLED margin and the gate grades
    software that is not the software being committed — Rule 12's hole, one
    import layer out.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO
    if extra:
        env.update(extra)
    return env
```

Delete the four `WRAPPER` checks (`src/cli_test.py:368-427`) and the `WRAPPER` constant at line 50. Replace them with a comment saying where that coverage went:

```python
# The bin/margin checks were here until 2026-10-12, and they are not replaced
# in this file. They tested that the launcher resolved its repo through a
# symlink from any directory — logic that was ours to get wrong. A pip console
# script is not ours, so there is nothing here to mutate or break. What
# survives of the property — "the command works from a directory that is not
# the repo" — is checked in src/package_test.py against an actually-installed
# wheel, which is the only place it can be true or false.
```

- [ ] **Step 6: Repoint `src/mutation_test.py` — the highest-risk edit in the plan**

Three changes. First, the tree it copies (`src/mutation_test.py:369`):

```python
        for directory in ("src", "margin"):
```

Second, all 30 product-file mutation paths. Exactly: 9 × `src/store.py`, 6 × `src/mcp_server.py`, 6 × `src/cli.py`, 3 × `src/table.py`, 3 × `src/compress.py`, 1 each of `src/store_commands.py`, `src/render.py`, `src/decompress.py`. Each `"src/<name>.py"` becomes `"margin/<name>.py"`:

```bash
sed -i '' -E 's|"src/(cli|compress|decompress|detect|render|store|store_commands|table|tokens|mcp_server)\.py"|"margin/\1.py"|g' src/mutation_test.py
grep -c '"margin/' src/mutation_test.py   # must print 30
grep -n '"src/' src/mutation_test.py      # must print nothing
```

Third, **delete the `bin/margin` mutation** at `src/mutation_test.py:344-350` ("bin/margin loses its symlink-resolution loop"), since the file is gone, and leave a comment in its place:

```python
    # "bin/margin loses its symlink-resolution loop" was here until 2026-10-12.
    # Retired with the file, not moved: a pip console script resolves the
    # command, so there is no symlink loop of ours left to break. MUTATIONS is
    # 30 here and 34 after package_test.py's four land.
```

Fourth, both subprocess spawns need the staged tree on `PYTHONPATH`, or every mutation run imports an installed `margin` and the mutated source is never executed — which would make every mutation SURVIVE, or worse, pass. At `src/mutation_test.py:383` and `:421`:

```python
        env = dict(os.environ)
        env["PYTHONPATH"] = tree
        done = subprocess.run([sys.executable, os.path.join(tree, "src", mutation.gate)],
                              capture_output=True, text=True, check=False, env=env)
```

- [ ] **Step 7: Update the two hooks and the workflow**

`.claude/hooks/run_eval.sh` — the `case` must match the package, and `bin/margin` is gone:

```bash
case "$file_path" in
  "$PROJECT_DIR"/src/*.py) ;;
  "$PROJECT_DIR"/margin/*.py) ;;
  *) exit 0 ;;
esac
```

Replace that file's `bin/margin` comment paragraph with:

```bash
# margin/*.py is the product and src/*.py is the harness; both matter here. The
# bin/margin line was removed on 2026-10-12 with the file — a pip console
# script replaced it. Miss either directory and editing the product runs no
# gate at all, which is this hook's own recorded failure (Rule 12).
```

`.githooks/pre-commit` — the staged-file filter:

```bash
staged_src=$(git diff --cached --name-only --diff-filter=ACM \
  | grep -E '^(src/.*\.py|margin/.*\.py)$' || true)
```

and, critically, the gates it runs out of the scratch tree need that tree importable:

```bash
# PYTHONPATH, or this gate grades the wrong software while reporting green.
# The scratch tree holds the staged margin/ package, but `import margin` inside
# it would resolve to whatever is installed on this machine — so a commit that
# breaks the product would pass against an older installed copy. Rule 12, one
# import layer out from where this hook already records it.
export PYTHONPATH="$STAGED_SRC"
```

placed immediately after `SRC="$STAGED_SRC/src"`.

`.github/workflows/gates.yml` — add `PYTHONPATH` to the job so the five gates import the checked-out package:

```yaml
    env:
      PYTHONPATH: ${{ github.workspace }}
```

directly under `timeout-minutes: 10`.

- [ ] **Step 8: Re-run the hook installer**

```bash
./.githooks/install.sh
```

Hooks are copied into `.git/hooks`, not symlinked — an edit does nothing until this runs.

- [ ] **Step 9: Run the full harness and verify it is green**

```bash
cd "/Users/navyshukla/Margin Project"
source .venv/bin/activate
export PYTHONPATH="$PWD"
python src/property_test.py && python src/cli_test.py && python src/mcp_test.py \
  && python src/eval_model.py --self-test && python src/mutation_test.py
for s in data/samples/*.json; do python src/eval_harness.py "$s" || break; done
```

Expected: all pass. **`mutation_test.py` is the one that matters most here** — it is the only thing that proves the 30 repointed paths still find their anchors. If it reports `anchor text not found`, a path or an anchor is wrong; if it reports mutations SURVIVED, `PYTHONPATH` is not reaching the subprocess.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Move the product into a package; the harness stays put

bin/margin's own comment declined packaging because src/ was flat modules
importing by bare name. That argument held while nothing was packaged;
once pip install is the goal it inverts.

The seventeen harness files did not move. They import margin.* now, which
is one line each, instead of being restructured for a benefit they do not
receive.

Three things that would have failed green:
- mutation_test copied src/ and bin/ and names 30 targets by path. Both
  repointed; a tester that stops finding its anchors passes green.
- every gate subprocess needs PYTHONPATH at its own tree, or it imports an
  installed margin and grades software that is not being committed.
- run_eval.sh's case matched src/*.py only, so editing the product would
  have run no gate at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 2: `pyproject.toml` and the console script

**Files:**
- Create: `pyproject.toml`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: the `margin` package from Task 1.
- Produces: a buildable wheel exposing the `margin` command → `margin.cli:main`. Task 3 builds it.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
# Packaging, added 2026-10-12. bin/margin is gone; this file replaces it.
#
# hatchling rather than setuptools: it needs no MANIFEST.in to ship
# margin/web/index.html — everything under the package directory goes into the
# wheel by default, and a UI whose one HTML file is missing from the wheel is
# the first failure src/package_test.py was written to catch.

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "margin"
version = "1.0.0"
description = "Compress JSON API responses before they go into an LLM prompt"
readme = "README.md"
license = "MIT"
requires-python = ">=3.12"
authors = [{ name = "Naivedya Shukla" }]
keywords = ["json", "compression", "llm", "tokens", "mcp"]

# Pinned exactly, matching requirements.txt and for the same reason: the
# numbers in docs/shapes.md are token counts, and a token count is a fact about
# a tokeniser as much as about a payload. A floating tiktoken would move every
# figure in this repo without a line of code changing.
#
# google-genai is NOT here. It is used by src/eval_model.py, which is harness
# and is not shipped, and only on its networked path. A user who installs this
# to compress JSON should not acquire a Gemini SDK.
dependencies = [
  "tiktoken==0.14.0",
  "mcp==2.2.0",
]

[project.optional-dependencies]
dev = ["google-genai==2.22.0"]

[project.scripts]
margin = "margin.cli:main"

[project.urls]
Homepage = "https://github.com/navyyshukla/Margin"

[tool.hatch.build.targets.wheel]
packages = ["margin"]
```

- [ ] **Step 2: Point `requirements.txt` at the project**

Keep the file — the hooks, CI and `docs/` all reference it — and make it stop being a second list that can drift:

```
# The runtime dependencies now live in pyproject.toml, which is the single
# list; this file installs the project itself plus the dev extra the harness
# needs. Two hand-maintained lists of the same pins is how they diverge.
#
# -e so the harness imports the working tree rather than a copy: src/*.py does
# `from margin.compress import ...`, and a non-editable install would have the
# gates grading whatever was installed last.
-e .[dev]
```

- [ ] **Step 3: Rebuild the venv against it and verify the command exists**

```bash
cd "/Users/navyshukla/Margin Project"
rm -rf .venv && uv venv .venv --python 3.12 && uv pip install -r requirements.txt
.venv/bin/margin --help
echo '[{"a":1,"b":2},{"a":3,"b":4},{"a":5,"b":6}]' | .venv/bin/margin
```

Expected: `--help` prints the usage line and exits 0; the payload prints a `#margin/v1` document. The editable install means `PYTHONPATH` is no longer needed for day-to-day runs, though the gates still set it for the reason Task 1 Step 6 gives.

- [ ] **Step 4: Run the harness**

```bash
source .venv/bin/activate && python src/property_test.py && python src/cli_test.py \
  && python src/mcp_test.py && python src/mutation_test.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "pyproject: one pinned list, and google-genai stops shipping

requirements.txt becomes '-e .[dev]' rather than a second copy of the
pins — two hand-maintained lists of the same dependencies is how they
diverge, and the docs quote these numbers.

google-genai moves to a dev extra. It is used by one harness script on
its networked path; nobody installing this to compress JSON should
acquire a Gemini SDK to do it.

hatchling, not setuptools: it ships margin/web/index.html without a
MANIFEST.in, and a wheel missing that file is the first thing
package_test.py exists to catch.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 3: `src/package_test.py` — the gate that answers "does it work when shipped"

TDD applies in its real form here: the gate is written first and must **fail for a real reason** before the things it checks exist.

**Files:**
- Create: `src/package_test.py`

**Interfaces:**
- Consumes: `pyproject.toml` from Task 2.
- Produces: `python src/package_test.py` → exit 0 when the built wheel installs and works, 1 otherwise. Task 4 mutates against it. Tasks 5–7 add checks to it as `margin mcp` and `margin web` appear.

- [ ] **Step 1: Write the gate, with the checks that can pass today**

```python
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
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Three records, enough to clear MIN_ROWS_TO_TABULATE, with a constant column
# so the document exercises #const rather than only the header. Inline because
# data/samples/ is gitignored and this gate has to run in CI.
PAYLOAD = json.dumps([
    {"id": 1, "state": "open", "title": "first"},
    {"id": 2, "state": "open", "title": "second"},
    {"id": 3, "state": "open", "title": "third"},
])

failures = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(name)


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
    """A venv with the wheel in it and NOTHING else. No --system-site-packages,
    no requirements.txt, no path into REPO: if the wheel does not carry a file,
    the command will not find it."""
    subprocess.run(["uv", "venv", venv, "--python", "3.12"],
                   capture_output=True, text=True, check=True)
    done = subprocess.run(["uv", "pip", "install", "--python", venv, wheel],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"install failed:\n{done.stdout}\n{done.stderr}")
    return os.path.join(venv, "bin", "margin")


def run(margin, args, stdin=None, cwd=None):
    """The installed command, from a directory that is not the repo.

    cwd defaults to the temp dir on purpose: `margin` has to work from
    anywhere, which is the one property bin/margin's retired checks tested.
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
        import zipfile
        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        check("margin/web/index.html is in the wheel",
              "margin/web/index.html" in names,
              f"wheel holds {len([n for n in names if n.startswith('margin/')])} margin/ files")

        try:
            margin = install(wheel, os.path.join(tmp, "venv"))
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            check("the wheel installs into a clean venv", False, str(exc))
            return 1
        check("the wheel installs into a clean venv", True)
        check("the console script exists", os.path.exists(margin), margin)

        code, out, err = run(margin, ["--help"])
        check("margin --help exits 0 from outside the repo",
              code == 0 and b"usage: margin" in out, f"exit {code}, stderr={err[:200]!r}")

        code, out, err = run(margin, ["-"], stdin=PAYLOAD)
        check("the installed command compresses",
              code == 0 and out.startswith(b"#margin/v1"),
              f"exit {code}, stdout={out[:120]!r}, stderr={err[:200]!r}")

        # Rule 1's shape: a document is not proof the pipeline ran unless it
        # comes back. Round-trip through the installed package, not this repo's.
        py = os.path.join(os.path.dirname(margin), "python")
        done = subprocess.run(
            [py, "-c",
             "import sys,json;from margin.decompress import decompress;"
             "from margin.table import same_json;"
             "print(same_json(decompress(sys.stdin.read()), json.loads(sys.argv[1])))",
             PAYLOAD],
            input=out.decode(), capture_output=True, text=True, check=False)
        check("the document round-trips inside the installed package",
              done.stdout.strip() == "True",
              f"stdout={done.stdout!r} stderr={done.stderr[:300]!r}")

        # google-genai must not have come along. `uv pip list` in that venv is
        # the only honest way to ask.
        done = subprocess.run(["uv", "pip", "list", "--python",
                               os.path.join(tmp, "venv"), "--format", "json"],
                              capture_output=True, text=True, check=False)
        installed = {p["name"].lower() for p in json.loads(done.stdout or "[]")}
        check("google-genai did not ship", "google-genai" not in installed,
              f"installed: {sorted(installed)}")
        check("tiktoken and mcp did ship",
              {"tiktoken", "mcp"} <= installed, f"installed: {sorted(installed)}")

        code, out, err = run(margin, ["fsck"])
        check("margin fsck runs from the installed command", code in (0, 1),
              f"exit {code}, stderr={err[:200]!r}")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all packaged-install checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it and watch the right check fail**

```bash
cd "/Users/navyshukla/Margin Project" && .venv/bin/python src/package_test.py
```

Expected: **`FAIL  margin/web/index.html is in the wheel`** and everything else `ok`. That one failure is correct — the file does not exist until Task 8. Every other check passing is what proves Tasks 1 and 2 actually shipped something installable.

If anything else fails, fix that before continuing; it is a real packaging bug caught exactly where intended.

- [ ] **Step 3: Make the HTML check conditional, with an expiry**

The gate cannot be committed red, and it must not permanently excuse the missing file:

```python
        # Conditional ONLY until Task 8 of
        # docs/superpowers/plans/2026-10-12-shipping-and-local-ui.md lands.
        # Delete this branch with that task — a check that cannot fail is not a
        # check (Rule 14), and this one is the first failure this gate exists
        # to catch.
        ui_exists = os.path.exists(os.path.join(REPO, "margin", "web", "index.html"))
        if ui_exists:
            check("margin/web/index.html is in the wheel",
                  "margin/web/index.html" in names, ...)
        else:
            print("  --    margin/web/index.html: not written yet (Task 8)")
```

- [ ] **Step 4: Run it again — green**

```bash
.venv/bin/python src/package_test.py; echo "exit $?"
```

Expected: `all packaged-install checks pass`, exit 0.

- [ ] **Step 5: Wire it into both hooks and CI**

In `.githooks/pre-commit`, after the MCP contract block:

```bash
# The fourth process boundary, and the only one that is not this repo: it
# builds a wheel, installs it into a throwaway venv and drives the installed
# command. ~20s, which is why it is here and not in the per-edit hook.
echo "pre-commit: running packaged install (builds a wheel)..."
if ! "$PYTHON" "$SRC/package_test.py"; then
  echo "" >&2
  echo "pre-commit: the packaged install failed — commit refused." >&2
  echo "  The wheel does not build, does not install clean, or the installed" >&2
  echo "  command no longer works. See src/package_test.py." >&2
  exit 1
fi
```

In `.github/workflows/gates.yml`, a sixth step after `MCP contract`:

```yaml
      # Does the thing a stranger installs actually work? Builds a wheel and
      # installs it into a clean venv. Runs here as well as locally because
      # this runner is Linux and development happens on macOS.
      - name: packaged install
        run: .venv/bin/python src/package_test.py
```

**Not** added to `.claude/hooks/run_eval.sh`: it builds a wheel on every call, and the per-edit chain is 17.0s by deliberate design. Add this comment there, next to the `mutation_test.py` paragraph:

```bash
# src/package_test.py deliberately does NOT run here either, for mutation
# _test.py's reason: it builds a wheel and installs it, which is ~20s on every
# edit. It runs at the commit boundary and on every push. A gate that makes the
# loop painful is a gate that gets bypassed, and a bypassed gate reports green
# while guarding nothing.
```

Then re-run the installer:

```bash
./.githooks/install.sh
```

- [ ] **Step 6: Commit**

```bash
git add src/package_test.py .githooks/pre-commit .github/workflows/gates.yml .claude/hooks/run_eval.sh
git commit -m "A ninth gate: install the wheel the way a stranger would

The eight gates all run inside this repo with a hand-built .venv, so
every one of them can pass while the shipped artifact is broken. A
missing index.html, a surviving bare-name import, a misdeclared console
script and a stray google-genai are all invisible from in here.

So: build a real wheel, install it into a venv with no path back into
this repo, and drive the installed command from a directory that is not
the repo — which is also what is left of bin/margin's retired checks.

Commit-boundary and CI only, not per-edit: it builds a wheel, and the
per-edit chain is 17s on purpose.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 4: Four mutations, so the new gate is a detector

Rule 14: a detector nobody has shown a positive to is not a detector. Rule 13: the baseline must be green first, and the mutation must name the gate that catches it.

**Files:**
- Modify: `src/mutation_test.py`

**Interfaces:**
- Consumes: `src/package_test.py` from Task 3, `pyproject.toml` from Task 2.
- Produces: `MUTATIONS` at 34. No new callable.

- [ ] **Step 1: Confirm the baseline is green**

```bash
cd "/Users/navyshukla/Margin Project" && .venv/bin/python src/mutation_test.py
```

Expected: 30 mutations, all caught. Rule 13 — a mutation added to a red baseline proves nothing.

- [ ] **Step 2: Add the four mutations**

Append to `MUTATIONS` in `src/mutation_test.py`:

```python
    # The four below target packaging, and all four name package_test.py —
    # nothing else in this repo can see them. Each one is a failure that would
    # reach a user as a broken `pip install margin` while all eight older gates
    # stayed green.
    Mutation(
        "the wheel stops shipping the UI's only file",
        "pyproject.toml",
        '[tool.hatch.build.targets.wheel]\npackages = ["margin"]',
        '[tool.hatch.build.targets.wheel]\npackages = ["margin"]\nexclude = ["web/*"]',
        "index.html is in the wheel",
        gate="package_test.py",
    ),
    Mutation(
        "a bare-name import survives the package move",
        "margin/cli.py",
        "from .detect import detect_content_type",
        "from detect import detect_content_type",
        "the installed command compresses",
        gate="package_test.py",
    ),
    Mutation(
        "the console script points at nothing",
        "pyproject.toml",
        'margin = "margin.cli:main"',
        'margin = "margin.cli:nonexistent_entry"',
        "margin --help exits 0",
        gate="package_test.py",
    ),
    Mutation(
        "google-genai goes back into the runtime dependencies",
        "pyproject.toml",
        '  "mcp==2.2.0",\n]',
        '  "mcp==2.2.0",\n  "google-genai==2.22.0",\n]',
        "google-genai did not ship",
        gate="package_test.py",
    ),
```

- [ ] **Step 3: `pyproject.toml` must be in the copied tree, or all four test nothing**

`src/mutation_test.py:369` copies directories. `pyproject.toml` is a file at the root, so it needs its own line in both the mutation runner and the baseline runner:

```python
        for directory in ("src", "margin"):
            shutil.copytree(os.path.join(REPO, directory), os.path.join(tree, directory),
                            ignore=shutil.ignore_patterns("__pycache__"))
        # pyproject.toml is a file, not a directory, and three of the four
        # packaging mutations target it. Without this they would hit a missing
        # path and report "anchor text not found", which this file treats as a
        # loud failure rather than a skip — correct, but for the wrong reason.
        shutil.copy2(os.path.join(REPO, "pyproject.toml"),
                     os.path.join(tree, "pyproject.toml"))
        shutil.copy2(os.path.join(REPO, "README.md"),
                     os.path.join(tree, "README.md"))
```

`README.md` too: `pyproject.toml` declares `readme = "README.md"` and the build fails without it.

- [ ] **Step 4: Run it and watch all four get caught**

```bash
.venv/bin/python src/mutation_test.py
```

Expected: 34 mutations, 0 survived. Each of the four must be caught **by the check named for it** — if one reports SURVIVED, `package_test.py` is not actually asking that question and the gate, not the mutation, is what needs fixing.

- [ ] **Step 5: Commit**

```bash
git add src/mutation_test.py
git commit -m "Show package_test.py four positives

31 - 1 + 4 = 34. The retired one is bin/margin's symlink loop, gone with
the file; a pip console script is not ours to break.

Each of the four is a broken \`pip install margin\` that leaves all eight
older gates green: a wheel without the UI's only file, a surviving
bare-name import, a dead console script, and google-genai riding along.

pyproject.toml and README.md now get copied into the mutation tree —
three of the four target the former and hatchling needs the latter, and
without them the mutations would report a missing anchor rather than
testing anything.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 5: `margin mcp`

**Files:**
- Modify: `margin/cli.py`, `src/package_test.py`, `src/mutation_test.py`, `docs/cli.md`

**Interfaces:**
- Consumes: `margin.mcp_server.main()`.
- Produces: `margin mcp` runs the stdio server. `margin.cli.LAZY_COMMANDS: dict[str, str]` maps a subcommand to the module holding its `main`.

- [ ] **Step 1: Add the check to `src/package_test.py` first, and watch it fail**

In `main()`, before the `fsck` check:

```python
        # JSON-RPC over a pipe, against the INSTALLED server. src/mcp_test.py
        # already drives the protocol; what this asks is narrower and only
        # answerable here — that `margin mcp` is reachable at all once the code
        # is inside a tool venv whose path nobody can predict.
        hello = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "package_test", "version": "0"}},
        }) + "\n"
        done = subprocess.run([margin, "mcp"], input=hello.encode(),
                              capture_output=True, timeout=30,
                              cwd=os.path.dirname(margin), check=False)
        check("margin mcp answers an initialize over stdio",
              b'"result"' in done.stdout,
              f"stdout={done.stdout[:200]!r} stderr={done.stderr[:300]!r}")
```

Run it:

```bash
.venv/bin/python src/package_test.py
```

Expected: `FAIL  margin mcp answers an initialize over stdio` — `margin mcp` is currently read as a filename.

- [ ] **Step 2: Add the dispatch**

In `margin/cli.py`, after the `USAGE` constant:

```python
# Subcommands whose module is imported only when the word is used.
#
# Lazy on purpose: `import mcp` pulls a dependency tree that the everyday
# `curl ... | margin | pbcopy` has no use for, and the everyday pipeline is
# what this CLI is shaped around. Measure before moving either of these to a
# module-level import.
#
# `web` lands in Task 7 of the shipping plan; `mcp` is here because packaging
# took its old entry point away — a user used to point their MCP client at a
# path inside the repo, and after `pip install` there is no such path. That is
# the same bar the four store subcommands cleared and `--decompress` did not:
# refusing this costs the feature, not keystrokes (docs/cli.md).
LAZY_COMMANDS = {"mcp": "margin.mcp_server"}
```

and in `main()`, immediately after the `store_commands` dispatch block:

```python
    if argv and argv[0] in LAZY_COMMANDS:
        import importlib

        # Same promise as every other path here: report what broke on stderr
        # rather than printing a traceback over the user's terminal. 1, not 2 —
        # it was called correctly and failed.
        try:
            importlib.import_module(LAZY_COMMANDS[argv[0]]).main()
            return 0
        except BrokenPipeError:
            raise
        except Exception as exc:                       # noqa: BLE001
            print(f"margin: {argv[0]} failed ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            return 1
```

Update `USAGE` to name it:

```python
USAGE = ("usage: margin [-q] [--store] [<file.json>|-]   (with no path, reads stdin)\n"
         "       margin fsck | gc | export | import | mcp")
```

- [ ] **Step 3: Run the gate and the harness**

```bash
.venv/bin/python src/package_test.py && .venv/bin/python src/cli_test.py
```

Expected: both pass. `cli_test.py` matters here because `USAGE` changed and it asserts on the usage line.

- [ ] **Step 4: Add the mutation**

```python
    Mutation(
        "margin mcp is treated as a filename again",
        "margin/cli.py",
        '    if argv and argv[0] in LAZY_COMMANDS:',
        '    if False:',
        "margin mcp answers an initialize",
        gate="package_test.py",
    ),
```

Run `.venv/bin/python src/mutation_test.py` — expected 35 mutations, 0 survived.

- [ ] **Step 5: Record the subcommand's cost in `docs/cli.md`**

In the section "The four store subcommands, and why they are not the same call as `--decompress`", add:

```markdown
**`margin mcp` (2026-10-12) is a fifth, and it clears the same bar.** Before
packaging, an MCP client was pointed at a path inside this repo. After
`pip install margin` there is no such path — the code is inside a tool venv
nobody can predict — so refusing this subcommand does not cost keystrokes, it
costs the feature. Same test the four above passed.

It inherits the ambiguity stated above: a file literally named `mcp` is now
read as the subcommand. `margin ./mcp` and `margin < mcp` still work.
```

- [ ] **Step 6: Commit**

```bash
git add margin/cli.py src/package_test.py src/mutation_test.py docs/cli.md
git commit -m "margin mcp: the entry point packaging took away

An MCP client used to be pointed at a path inside this repo. After pip
install there is no such path, so this clears the bar docs/cli.md set
when it refused --decompress: refusing it costs the feature, not
keystrokes.

Imported lazily — \`import mcp\` pulls a dependency tree that
\`curl ... | margin | pbcopy\` has no use for.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 6: `compress_json` reports what it dropped

The UI's one honesty line needs this, and nothing currently reports it (verified 2026-10-12: no "dropped" string reaches `cli.py`).

**Files:**
- Modify: `margin/compress.py`, `src/property_test.py`, `src/mutation_test.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `margin.compress.strip_boilerplate(data, drop_bare_url=None, dropped=None)` — `dropped`, when a `set` is passed, is filled with the key names removed. `compress_json` returns `(text, notes)` unchanged in shape, with one added note when anything was dropped: `"dropped N key(s) not recoverable: a, b, c"`. Task 7 parses that note.

- [ ] **Step 1: Write the failing check in `src/property_test.py`**

Add to the fixed cases section:

```python
def check_dropped_keys_are_reported():
    """The lossy stage has to say what it took.

    Rule 3's shape: round-trip equality cannot see this. decompress() never had
    these keys and never will, so the round-trip is exactly as green with the
    note as without it — and the UI prints that note to someone who did not
    read any docs before clicking Copy. CLAUDE.md's first rule is reversibility
    and 'never delete-and-hope'; an unreported deletion is the second half of
    that.
    """
    payload = [
        {"id": 1, "html_url": "http://x/1", "node_id": "n1", "title": "a"},
        {"id": 2, "html_url": "http://x/2", "node_id": "n2", "title": "b"},
        {"id": 3, "html_url": "http://x/3", "node_id": "n3", "title": "c"},
    ]
    text, notes = compress_json(payload)
    reported = [n for n in notes if n.startswith("dropped ")]
    check("the drop stage reports its keys", len(reported) == 1, f"notes={notes}")
    if reported:
        check("it names the keys it took",
              "html_url" in reported[0] and "node_id" in reported[0],
              reported[0])
    # And says nothing when it took nothing — a note that always fires is not
    # a note (Rule 14).
    clean = [{"id": 1, "t": "a"}, {"id": 2, "t": "b"}, {"id": 3, "t": "c"}]
    _, clean_notes = compress_json(clean)
    check("silent when nothing was dropped",
          not [n for n in clean_notes if n.startswith("dropped ")],
          f"notes={clean_notes}")
```

Call it from the gate's `main()` alongside the other fixed-case checks.

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv/bin/python src/property_test.py
```

Expected: `FAIL  the drop stage reports its keys`.

- [ ] **Step 3: Implement**

In `margin/compress.py`, thread a set through `strip_boilerplate`:

```python
def strip_boilerplate(data, drop_bare_url=None, dropped=None):
    """Recursively drop link-template and opaque-ID keys from dicts/lists.

    drop_bare_url is decided once for the whole document and passed down; see
    uses_url_template_convention.

    `dropped`, when a set is passed, collects the key NAMES removed — not the
    values, which is the point: this stage is the one irreversible thing in the
    pipeline and the names are what a caller needs to say what is gone. Nothing
    reported this until 2026-10-12, which was defensible while the only caller
    was a CLI whose user had read docs/shapes.md to get here, and is not
    defensible for a page with a Copy button on it.
    """
    if drop_bare_url is None:
        drop_bare_url = uses_url_template_convention(data)
    if isinstance(data, dict):
        if dropped is not None:
            dropped.update(k for k in data
                           if k.endswith("_url") or k in NOISE_KEYS
                           or (k == "url" and drop_bare_url))
        return {
            k: strip_boilerplate(v, drop_bare_url, dropped)
            for k, v in data.items()
            if not k.endswith("_url")
            and k not in NOISE_KEYS
            and not (k == "url" and drop_bare_url)
        }
    if isinstance(data, list):
        return [strip_boilerplate(v, drop_bare_url, dropped) for v in data]
    return data
```

In `compress_json`, line 170 onward:

```python
    dropped = set()
    stripped = strip_boilerplate(data, dropped=dropped)
    as_json = json.dumps(stripped, separators=COMPACT)

    # Prepended to every return path's notes, including the fallbacks: what the
    # lossy stage took is true of the payload, not of whichever later stage won.
    lost = ([f"dropped {len(dropped)} key(s) not recoverable: "
             f"{', '.join(sorted(dropped))}"] if dropped else [])
```

and every `return result(...)` / final return gains `lost +` before its notes list. Find them all:

```bash
grep -n "return result(\|return text, notes\|notes + \[" margin/compress.py
```

- [ ] **Step 4: Run the gate — green**

```bash
.venv/bin/python src/property_test.py
```

Expected: pass, including the three new checks.

- [ ] **Step 5: Verify the note reaches the CLI, and does not reach stdout**

```bash
printf '[{"id":1,"html_url":"http://x/1","t":"a"},{"id":2,"html_url":"http://x/2","t":"b"},{"id":3,"html_url":"http://x/3","t":"c"}]' \
  | .venv/bin/margin 2>/tmp/margin-err >/tmp/margin-out
grep -c "dropped 1 key" /tmp/margin-err   # 1
grep -c "dropped" /tmp/margin-out         # 0
```

Expected: `1` then `0`. The second is the one that matters — anything on stdout ends up on the clipboard.

- [ ] **Step 6: Add the mutation and run the suite**

```python
    Mutation(
        "the drop stage stops reporting what it took",
        "margin/compress.py",
        "    dropped = set()\n    stripped = strip_boilerplate(data, dropped=dropped)",
        "    dropped = set()\n    stripped = strip_boilerplate(data)",
        "the drop stage reports its keys",
        gate="property_test.py",
    ),
```

```bash
.venv/bin/python src/mutation_test.py
```

Expected: 36 mutations, 0 survived.

- [ ] **Step 7: Commit**

```bash
git add margin/compress.py src/property_test.py src/mutation_test.py
git commit -m "The lossy stage says what it took

strip_boilerplate drops *_url, node_id and gravatar_id and nothing
restores them, and until now nothing reported it either — the CLI is
silent and the docs carry the warning instead.

Defensible for a CLI whose user read docs/shapes.md to get there. Not
defensible for a page with a Copy button, which is what Task 7 adds.
CLAUDE.md's first rule is reversibility and never delete-and-hope; an
unreported deletion is the second half of that.

Round-trip cannot see this (Rule 3) — decompress never had these keys —
so it gets a check aimed at the claim, including that it stays silent
when nothing was dropped.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 7: `margin web` — the server

All five Review Focus items live here.

**Files:**
- Create: `margin/web.py`, `src/web_test.py`
- Modify: `margin/cli.py` (`LAZY_COMMANDS`), `src/mutation_test.py`, `.githooks/pre-commit`, `.claude/hooks/run_eval.sh`, `.github/workflows/gates.yml`

**Interfaces:**
- Consumes: `margin.compress.compress_json`, `margin.detect.detect_content_type`, `margin.cli.summary`.
- Produces: `margin.web.compress_payload(raw: bytes) -> dict` with keys `document` (str), `before` (int), `after` (int), `percent` (float), `note` (str|None), `dropped` (list[str]); `margin.web.bind(port=DEFAULT_PORT) -> http.server.ThreadingHTTPServer`; `margin.web.main() -> None`.

- [ ] **Step 1: Write `src/web_test.py` with every check, including the five Review Focus ones**

```python
"""Does the page keep the CLI's promises?

The UI calls compress_json, the same function cli.py calls, so this gate does
not re-ask whether compression works — property_test.py owns that. It asks the
questions that only exist at the HTTP boundary, which is the fourth process
boundary in this repo and Rule 11's argument one more time: argv, streams and
exit codes only exist at a process boundary, and a request body, a status code
and a Content-Length only exist at this one.

The CLI has already settled what happens to input that is not JSON, not UTF-8,
or too deep to parse. This gate's real job is making sure the page AGREES with
it rather than inventing a second policy.

Run: python src/web_test.py
"""

import json
import threading
import urllib.error
import urllib.request

from margin import web

failures = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(name)


TABULATES = json.dumps([
    {"id": 1, "state": "open", "title": "first"},
    {"id": 2, "state": "open", "title": "second"},
    {"id": 3, "state": "open", "title": "third"},
])


def post(url, body, headers=None):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    print("web contract\n")
    server = web.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            page = r.read()
        check("GET / serves the page", r.status == 200 and b"<title>" in page)
        check("the page loads nothing from the network",
              b"//cdn" not in page and b"https://" not in page.replace(b"https://127.0.0.1", b""),
              "a CDN or font reference would break it offline")

        status, body = post(base + "/compress", TABULATES.encode())
        got = json.loads(body)
        check("POST /compress compresses", status == 200
              and got["document"].startswith("#margin/v1"), f"{status} {body[:200]!r}")
        check("it reports both counts and a percent",
              got["before"] > got["after"] > 0 and 0 < got["percent"] < 100, str(got)[:200])

        # The UI's numbers must BE the CLI's numbers, not a second calculation.
        from margin.compress import compress_json
        text, _ = compress_json(json.loads(TABULATES), original_text=TABULATES)
        check("the page's document is byte-identical to the library's",
              got["document"] == text, "the UI has drifted from the compressor")

        # --- the CLI's settled passthrough policy, which the page must share ---
        status, body = post(base + "/compress", b"<html>not json</html>")
        got = json.loads(body)
        check("not JSON: 200, bytes unchanged, and it says so",
              status == 200 and got["document"] == "<html>not json</html>"
              and "not JSON" in (got["note"] or ""), f"{status} {str(got)[:200]}")

        # Review Focus 4
        status, body = post(base + "/compress", b"\xff\xfe\x00not utf-8")
        got = json.loads(body)
        check("not UTF-8: 200 and a note, not a 500",
              status == 200 and "UTF-8" in (got["note"] or ""), f"{status} {body[:200]!r}")

        status, body = post(base + "/compress", b'{"a":1}')
        got = json.loads(body)
        check("nothing worth compressing: 0.0% is not an error",
              status == 200 and got["percent"] == 0.0, str(got)[:200])

        # Both recursions, because docs/cli.md records that one payload can only
        # ever reach one of them: ~3x the limit parses then dies downstream, 10x
        # dies inside json.loads itself.
        import sys
        for depth, where in ((int(sys.getrecursionlimit() * 3), "downstream"),
                             (sys.getrecursionlimit() * 10, "in the parser")):
            deep = ("[" * depth) + ("]" * depth)
            status, body = post(base + "/compress", deep.encode())
            got = json.loads(body)
            check(f"{depth} deep ({where}): 200 and unchanged, no traceback",
                  status == 200 and got["document"] == deep, f"{status} {body[:160]!r}")

        # Review Focus 5
        status, body = post(base + "/compress", b"")
        check("empty body: 400, because there is no argv to be wrong about",
              status == 400, f"{status}")
        req = urllib.request.Request(base + "/nope", method="GET")
        try:
            urllib.request.urlopen(req, timeout=10)
            check("an unknown path 404s", False, "it returned 200")
        except urllib.error.HTTPError as e:
            check("an unknown path 404s", e.code == 404, f"{e.code}")

        # Review Focus 3
        cap = web.MAX_BODY_BYTES
        status, body = post(base + "/compress", b"x" * (cap + 1))
        check("a body over the cap: 413, not an OOM", status == 413, f"{status}")

        # Review Focus 2
        results = []
        def hammer():
            s, b = post(base + "/compress", TABULATES.encode())
            results.append(json.loads(b)["after"])
        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        check("8 concurrent requests all return the same correct count",
              len(results) == 8 and len(set(results)) == 1 and results[0] == got_after(TABULATES),
              f"results={results}")

        # The honesty line's data, from Task 6.
        with_urls = json.dumps([
            {"id": 1, "html_url": "http://x/1", "t": "a"},
            {"id": 2, "html_url": "http://x/2", "t": "b"},
            {"id": 3, "html_url": "http://x/3", "t": "c"},
        ])
        status, body = post(base + "/compress", with_urls.encode())
        got = json.loads(body)
        check("it names the keys the lossy stage took",
              got["dropped"] == ["html_url"], f"dropped={got.get('dropped')}")
    finally:
        server.shutdown()

    # Review Focus 1 — a second bind must not crash.
    second = web.bind(port=server.server_port)
    check("a taken port falls back rather than raising",
          second.server_port != 0, f"got {second.server_port}")
    second.server_close()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all web checks pass")
    return 0


def got_after(payload):
    from margin.compress import compress_json
    from margin.tokens import token_count
    text, _ = compress_json(json.loads(payload), original_text=payload)
    return token_count(text)


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 2: Run it and watch it fail on the import**

```bash
.venv/bin/python src/web_test.py
```

Expected: `ModuleNotFoundError: No module named 'margin.web'`.

- [ ] **Step 3: Write `margin/web.py`**

```python
"""margin web — the one page that shows what a payload saved.

The CLI prints a percentage on stderr, which is the right output for a pipe and
a poor way to see what this does. This serves one page: paste JSON, see the
document and the saving, copy it.

It calls compress_json — the same function cli.py calls, not a copy of it. That
is the whole design: there is exactly one compressor, so the eight gates that
guard it guard this too, and a pipeline change reaches the page for free. A
JavaScript reimplementation in the browser was considered and rejected for the
opposite reason, in the spec.

127.0.0.1 only, and there is deliberately no --host flag. 0.0.0.0 would put an
unauthenticated endpoint that parses untrusted JSON onto the local network, and
docs/cli.md's standing pattern is to decline flags that buy keystrokes.

The passthrough policy here is NOT a second policy: not JSON, not UTF-8 and
too-deep-to-parse all behave the way cli.py settled them — bytes back
unchanged, a note saying why, and a success status. src/web_test.py asserts the
agreement rather than the behaviour.
"""

import json
import os
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .compress import compress_json
from .detect import detect_content_type
from .tokens import token_count

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "index.html")

DEFAULT_PORT = 8731
# Arbitrary, and said so rather than implied: this is not a measured number.
# It is unassigned in the IANA registry and stable enough to bookmark. A taken
# port falls back to an ephemeral one rather than failing — a second
# `margin web` is a thing people do, not an error.

MAX_BODY_BYTES = 64 * 1024 * 1024
# Arbitrary too, and a cap rather than a measurement: the point is only that an
# unbounded read of Content-Length is an OOM a browser can trigger. 64 MiB is
# ~300x the largest sample payload (github_issues.json, 50,031 tokens, ~210 KB),
# so it bounds the damage without being a limit anyone meets by accident.


def compress_payload(raw):
    """The CLI's pipeline, as a dict for the page. Never raises.

    Mirrors cli.main()'s order exactly — decode, parse, type-check, compress —
    because the two agreeing is the property src/web_test.py checks. Where the
    CLI writes a note to stderr and the bytes to stdout, this puts both in the
    response.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Lossy on the way to the page, and only because a JSON response has to
        # be text. The CLI hands back the original bytes; nothing here can, so
        # it says what happened and shows what it could decode.
        return {"document": raw.decode("utf-8", "replace"),
                "before": 0, "after": 0, "percent": 0.0,
                "note": "not UTF-8 — passed through unchanged", "dropped": []}

    def unchanged(note):
        return {"document": text, "before": 0, "after": 0, "percent": 0.0,
                "note": note, "dropped": []}

    # Two guards, because docs/cli.md records that there are two recursions and
    # a single payload can only ever reach one of them: json.loads raises
    # RecursionError itself past roughly 3x the limit, from inside
    # detect_content_type, which catches only JSONDecodeError; below that the
    # parse succeeds and the pipeline blows up further in. `except Exception`,
    # not RecursionError — the promise is about the pipeline, not about which
    # bugs were anticipated.
    try:
        content_type, data = detect_content_type(text)
    except Exception as exc:                           # noqa: BLE001
        return unchanged(f"could not parse ({type(exc).__name__}: {exc}) "
                         f"— passed through unchanged")
    if content_type != "json":
        return unchanged("not JSON — passed through unchanged")

    try:
        document, notes = compress_json(data, original_text=text)
    except Exception as exc:                           # noqa: BLE001
        return unchanged(f"could not compress ({type(exc).__name__}: {exc}) "
                         f"— passed through unchanged")

    before, after = token_count(text), token_count(document)
    dropped = []
    for note in notes:
        if note.startswith("dropped "):
            dropped = note.split(": ", 1)[1].split(", ")
    return {
        "document": document,
        "before": before,
        "after": after,
        # 0.0 rather than a negative: compress_json already guarantees the
        # output is never longer than the input, so a negative here would be a
        # bug, and max() is how it shows up as 0.0% instead of as a UI artifact.
        "percent": round(max(0.0, (1 - after / before) * 100), 1),
        "note": next((n for n in notes if not n.startswith("dropped ")), None),
        "dropped": dropped,
    }


class Handler(BaseHTTPRequestHandler):
    """Three routes. Anything else is a 404."""

    # Quiet by default: one line per request on a page that fires one request
    # per keystroke-pause is noise, and the terminal is where the URL is.
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page is served from this process and talks to nothing else; a
        # browser extension or another page has no business posting here.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/":
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        with open(PAGE, "rb") as f:
            self._send(200, f.read(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/compress":
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        # A missing or unparseable Content-Length is a 400, not a traceback:
        # curl, a probe and a half-closed browser connection all produce one.
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return self._send(400, b'{"error":"empty body"}',
                              "application/json; charset=utf-8")
        if length > MAX_BODY_BYTES:
            return self._send(413, b'{"error":"payload too large"}',
                              "application/json; charset=utf-8")
        body = json.dumps(compress_payload(self.rfile.read(length))).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")


def bind(port=DEFAULT_PORT):
    """A server on 127.0.0.1, falling back to an ephemeral port if `port` is taken.

    Returns the server rather than serving: src/web_test.py needs to run it on
    a thread and read back the port it actually got.
    """
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # Almost always a second `margin web`. Not an error worth refusing on —
        # take any free port and print the real URL.
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main():
    server = bind()
    url = f"http://127.0.0.1:{server.server_port}"
    # stderr, because every note in this project goes to stderr and this one is
    # not the document. Printed BEFORE opening the browser: if the browser call
    # fails or opens nothing, the URL is still on screen to click.
    print(f"margin: serving {url} — Ctrl-C to stop", file=__import__("sys").stderr)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", file=__import__("sys").stderr)
    finally:
        server.shutdown()
        server.server_close()
```

- [ ] **Step 4: Register the subcommand**

In `margin/cli.py`:

```python
LAZY_COMMANDS = {"mcp": "margin.mcp_server", "web": "margin.web"}
```

and extend `USAGE`:

```python
USAGE = ("usage: margin [-q] [--store] [<file.json>|-]   (with no path, reads stdin)\n"
         "       margin fsck | gc | export | import | mcp | web")
```

- [ ] **Step 5: Run `src/web_test.py` — it should fail only on the missing page**

```bash
.venv/bin/python src/web_test.py
```

Expected: `GET / serves the page` fails (`FileNotFoundError` → the handler raises); every other check passes. Task 8 writes the file.

- [ ] **Step 6: Commit the server**

```bash
git add margin/web.py margin/cli.py src/web_test.py
git commit -m "margin web: the server, calling the one compressor there is

The CLI prints a percentage on stderr, which is right for a pipe and a
poor way to see what this does.

compress_payload mirrors cli.main()'s order — decode, parse, type-check,
compress — because the two agreeing is what web_test.py checks. Not JSON,
not UTF-8 and too-deep all behave the way docs/cli.md settled them; this
is not a second policy.

127.0.0.1 only and no --host flag: 0.0.0.0 would put an unauthenticated
endpoint that parses untrusted JSON onto the local network.

Both recursion guards, a 64 MiB body cap, a 400 on a missing
Content-Length and an ephemeral-port fallback — a browser can trigger
every one of those and none of them may traceback into the terminal.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 8: `margin/web/index.html` — the page

**Files:**
- Create: `margin/web/index.html`
- Modify: `src/package_test.py` (delete the conditional from Task 3 Step 3), `src/mutation_test.py`

**Interfaces:**
- Consumes: `POST /compress` → `{document, before, after, percent, note, dropped}`.
- Produces: nothing importable.

- [ ] **Step 1: Write the page**

One file, no network, no build. Light and dark.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>margin</title>
<!--
  One file, and nothing loaded from the network: no CDN, no webfont, no
  analytics. The page works on a plane, which is the only sane property for a
  tool that runs on localhost.

  No framework either. The whole interaction is "post the textarea, render six
  numbers", and a build step for that would be a build step before every
  release of a thing with no build step today.
-->
<style>
  :root {
    --bg: #fcfcfa; --fg: #1a1a18; --dim: #6b6b64;
    --line: #e2e2dc; --accent: #2f6f4f; --warn: #8a5a1a;
    --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16160f; --fg: #ebebe4; --dim: #9a9a90;
      --line: #2e2e26; --accent: #7fc49b; --warn: #d9a85a;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif;
    display: flex; flex-direction: column; height: 100vh;
  }
  header {
    padding: 14px 16px; border-bottom: 1px solid var(--line);
    display: flex; align-items: baseline; gap: 10px;
  }
  h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .02em; }
  header span { color: var(--dim); font-size: 13px; }
  main { flex: 1; display: grid; grid-template-columns: 1fr 1fr; min-height: 0; }
  section { display: flex; flex-direction: column; min-width: 0; }
  section + section { border-left: 1px solid var(--line); }
  label {
    font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--dim); padding: 10px 16px 6px;
  }
  textarea, pre {
    flex: 1; margin: 0; padding: 0 16px 16px; border: 0; resize: none;
    background: transparent; color: var(--fg);
    font: 12px/1.6 var(--mono); overflow: auto; white-space: pre;
  }
  textarea:focus { outline: none; }
  footer { border-top: 1px solid var(--line); padding: 12px 16px; }
  .row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .count { font: 13px var(--mono); color: var(--dim); }
  .bar {
    flex: 1; min-width: 120px; height: 8px; border-radius: 4px;
    background: var(--line); overflow: hidden;
  }
  .bar i { display: block; height: 100%; width: 0; background: var(--accent);
           transition: width .25s ease; }
  .pct { font-weight: 600; font-variant-numeric: tabular-nums; min-width: 4.5em;
         text-align: right; }
  button {
    font: inherit; padding: 6px 14px; border: 1px solid var(--line);
    border-radius: 6px; background: transparent; color: var(--fg); cursor: pointer;
  }
  button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  button:disabled { opacity: .4; cursor: default; }
  .note { color: var(--dim); font-size: 13px; margin-top: 8px; }
  .warn { color: var(--warn); font-size: 13px; margin-top: 8px; }
  .warn code { font-family: var(--mono); }
  @media (max-width: 720px) {
    main { grid-template-columns: 1fr; }
    section + section { border-left: 0; border-top: 1px solid var(--line); }
  }
</style>
</head>
<body>
<header>
  <h1>margin</h1>
  <span>the room your context earns back</span>
</header>
<main>
  <section>
    <label for="in">your JSON</label>
    <textarea id="in" spellcheck="false" autofocus
      placeholder="paste a JSON API response"></textarea>
  </section>
  <section>
    <label for="out">compressed</label>
    <pre id="out"></pre>
  </section>
</main>
<footer>
  <div class="row">
    <span class="count" id="count"></span>
    <span class="bar"><i id="fill"></i></span>
    <span class="pct" id="pct"></span>
    <button id="copy" disabled>copy</button>
  </div>
  <div class="note" id="note"></div>
  <div class="warn" id="warn"></div>
</footer>
<script>
const $ = id => document.getElementById(id);
let timer, last = "";

// Debounced rather than per-keystroke: a paste fires one input event but a
// typed edit fires one per character, and each costs a tokenizer pass over the
// whole payload on the server.
$("in").addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(go, 250);
});

async function go() {
  const raw = $("in").value;
  if (raw === last) return;
  last = raw;
  if (!raw.trim()) return reset();
  let r;
  try {
    r = await (await fetch("/compress", { method: "POST", body: raw })).json();
  } catch (e) {
    // The server is this process. If the fetch fails it has gone away, and
    // saying so is more use than a silent empty panel.
    $("note").textContent = "margin is not responding — is it still running?";
    return;
  }
  if (r.error) { reset(); $("note").textContent = r.error; return; }

  $("out").textContent = r.document;
  $("count").textContent = r.before
    ? `${r.before.toLocaleString()} → ${r.after.toLocaleString()} tokens`
    : "";
  $("pct").textContent = r.before ? `${r.percent.toFixed(1)}%` : "";
  $("fill").style.width = `${r.percent}%`;
  $("copy").disabled = !r.document;
  $("note").textContent = r.note || "";

  // The one line of honesty. Shown only when the lossy stage actually fired,
  // because a warning that is always on is a warning nobody reads.
  $("warn").innerHTML = r.dropped && r.dropped.length
    ? `${r.dropped.length} link key${r.dropped.length > 1 ? "s" : ""} dropped and `
      + `not recoverable: ` + r.dropped.map(k => `<code>${k}</code>`).join(", ")
    : "";
}

function reset() {
  $("out").textContent = "";
  $("count").textContent = $("pct").textContent = $("note").textContent = "";
  $("warn").textContent = "";
  $("fill").style.width = "0";
  $("copy").disabled = true;
}

$("copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("out").textContent);
  $("copy").textContent = "copied";
  setTimeout(() => ($("copy").textContent = "copy"), 1200);
});
</script>
</body>
</html>
```

**Note for the implementer:** check the rendered colours in both schemes once the page is up (Task 8 Step 4). The two `--warn` values are the only tokens that carry meaning rather than decoration — the dropped-keys line uses them — so they need to stay legible against both backgrounds.

- [ ] **Step 2: Run the web gate — all green now**

```bash
.venv/bin/python src/web_test.py
```

Expected: every check passes, including `GET / serves the page` and `the page loads nothing from the network`.

- [ ] **Step 3: Delete the conditional from `src/package_test.py`**

Replace the `if ui_exists:` branch added in Task 3 Step 3 with the unconditional check:

```python
        check("margin/web/index.html is in the wheel",
              "margin/web/index.html" in names,
              f"wheel holds {len([n for n in names if n.startswith('margin/')])} margin/ files")
```

Then confirm the wheel really carries it:

```bash
.venv/bin/python src/package_test.py
```

Expected: pass, with that check now `ok` rather than skipped. **This is the moment hatchling's package-data behaviour is actually proven** rather than assumed.

- [ ] **Step 4: Look at it**

```bash
.venv/bin/margin web
```

Paste a real payload (`data/samples/github_issues.json`), confirm the bar and the percentage match what the CLI prints for the same file, and confirm the dropped-keys line appears for GitHub and does **not** appear for `data/samples/openmeteo_forecast.json`.

```bash
.venv/bin/margin data/samples/github_issues.json >/dev/null
```

The percentage on stderr must equal the one on the page.

- [ ] **Step 5: Wire `src/web_test.py` into the hooks and CI**

Add it to `.githooks/pre-commit` (after the MCP contract, before the packaged install), to `.claude/hooks/run_eval.sh` (it is sub-second and sample-free, so it belongs in the per-edit chain), and as a step in `.github/workflows/gates.yml`. Then:

```bash
./.githooks/install.sh
```

- [ ] **Step 6: Add the mutations and run the suite**

```python
    Mutation(
        "the page stops warning about keys it dropped",
        "margin/web.py",
        '        if note.startswith("dropped "):\n'
        '            dropped = note.split(": ", 1)[1].split(", ")',
        "        pass",
        "it names the keys the lossy stage took",
        gate="web_test.py",
    ),
    Mutation(
        "the body cap is removed",
        "margin/web.py",
        "        if length > MAX_BODY_BYTES:",
        "        if False:",
        "a body over the cap",
        gate="web_test.py",
    ),
    Mutation(
        "a taken port raises instead of falling back",
        "margin/web.py",
        "    except OSError:",
        "    except NotImplementedError:",
        "a taken port falls back",
        gate="web_test.py",
    ),
```

```bash
.venv/bin/python src/mutation_test.py
```

Expected: 39 mutations, 0 survived.

- [ ] **Step 7: Commit**

```bash
git add margin/web/index.html src/package_test.py src/mutation_test.py \
        .githooks/pre-commit .claude/hooks/run_eval.sh .github/workflows/gates.yml
git commit -m "The page itself, and the wheel is now proven to carry it

One file, nothing from the network, no framework: the whole interaction
is post a textarea and render six numbers, and a build step for that is
a build step before every release of something that has none.

package_test.py's index.html check stops being conditional, which is the
moment hatchling's package-data behaviour is proven rather than assumed.

The dropped-keys line appears only when the lossy stage fired — a
warning that is always on is a warning nobody reads.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 9: Docs, with every count re-derived

Rule 5: re-measure, never remember. Every number below is measured in Step 1 and only then written.

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/README.md`, `docs/cli.md`, `docs/harness.md`, `docs/status.md`

- [ ] **Step 1: Measure, do not remember**

```bash
cd "/Users/navyshukla/Margin Project"
ls margin/*.py | wc -l                                  # product files
ls src/*.py | wc -l                                     # harness files
grep -c "Mutation(" src/mutation_test.py                # mutations
ls src/*_test.py src/eval_harness.py src/eval_model.py  # gate files
PYTHONPATH="$PWD" .venv/bin/python -c "
import glob, importlib

# At IMPORT time, never by reading the list literals. checks_pokeapi_ditto.py
# appends a check after its own literal, so counting the source undercounts —
# docs/status.md records that this is how a count stayed wrong at 77.
total = 0
for path in sorted(glob.glob('src/checks_*.py')):
    name = path[4:-3]
    mod = importlib.import_module(name)
    checks = getattr(mod, 'CHECKS', None)
    if checks is None:
        raise SystemExit(f'{name}: no CHECKS attribute — read the module and fix this line')
    total += len(checks)
    print(f'{name}: {len(checks)}')
print('total checks:', total)
"
```

Write down what each command printed. If the last command raises, open one `src/checks_*.py`, find what the per-payload list is actually called, and use that name — guessing the attribute is how the count goes wrong in the first place.

- [ ] **Step 2: Rewrite the install sections**

`README.md`'s two install blocks become:

````markdown
```bash
pip install margin
```

```bash
curl -s https://api.github.com/repos/python/cpython/issues | margin | pbcopy
margin response.json > compressed.txt
margin web          # a page at 127.0.0.1 that shows what it saved
```
````

and the "Running it yourself" section keeps the clone instructions for anyone working **on** Margin, retitled "Working on it" with `uv venv .venv && uv pip install -r requirements.txt` (now `-e .[dev]`).

- [ ] **Step 3: Update `CLAUDE.md`'s Layout and Harness sections**

The file counts, the `src/` description, the gate count, and the two places that decline packaging. Replace the layout bullet with the measured numbers from Step 1, and **rewrite rather than delete** the packaging note:

```markdown
- `margin/` — the product, a real package: the pipeline (`compress`, `decompress`,
  `detect`, `render`, `store`, `table`, `tokens`) plus `cli.py`, `mcp_server.py`,
  `store_commands.py` and `web.py`, with the UI's single HTML file under
  `margin/web/`. Packaged as of 2026-10-12; `bin/margin` is gone. The note that
  used to live here said the `src/` + tests split was "a packaging benefit and
  nothing here is packaged" — correct until packaging became the goal, at which
  point the benefit had something to be a benefit to. The harness did **not**
  move, which is why the rename touched ten files and not twenty-seven.
- `src/` — the harness that guards it: the gates, the eval harness and its
  per-payload `checks_*.py`, the model-in-the-loop eval, and three measurement
  scripts. They import the product as `margin.*`.
```

- [ ] **Step 4: Update `docs/harness.md` and the skill's gate table**

Gates 8 → 10 (`src/web_test.py`, `src/package_test.py`), with the cadence column filled in honestly: `web_test` edit+commit+CI, `package_test` commit+CI only. Add the rule the plan earned, appended and **not renumbered**:

```markdown
## Rule 17 — a gate that runs inside the repo cannot see the thing you ship

Added 2026-10-12, when the product was packaged. All eight gates ran against
`src/` with a hand-built `.venv`, and every one of them could pass while
`pip install margin` was broken: a wheel missing the UI's only file, a surviving
bare-name import, a dead console script, a dev dependency riding along into a
user's environment. None of those exists until a build happens.

It is Rule 11 one layer out — the everyday invocation is a test case — with
"everyday" meaning what a stranger does, not what the author does. The gate
builds a real wheel, installs it into a venv with no path back into the repo,
and drives the installed command from a directory that is not the repo.

Corollary, which is Rule 12's shape: the gates' subprocesses need `PYTHONPATH`
pinned to the tree under test. Without it `import margin` resolves to whatever
is installed on the machine, and the gate grades software that is not the
software being committed — while reporting green.
```

Then update `.claude/skills/harness/SKILL.md`'s gate table and the "sixteen rules" heading to seventeen, including the count in its first paragraph.

- [ ] **Step 5: Add the stage to `docs/status.md`**

Dated, with the numbers from Step 1, and stating what is still open:

```markdown
## PR #12 — shipped, and a gate that can see the shipped thing (2026-10-12)

`docs/status.md` called this project finished on 2026-09-12, and against the
scope `CLAUDE.md` set it was. This is a new scope, chosen rather than drifted
into: a stranger can now `pip install margin`, and `margin web` shows them what
a payload saved.

- [x] **The product is a package.** Ten files moved to `margin/`; the seventeen
      harness files stayed in `src/` and import `margin.*`. `bin/margin` is
      gone, replaced by `[project.scripts]`.
- [x] **A ninth and tenth gate.** `src/package_test.py` builds a wheel and
      installs it into a clean venv; `src/web_test.py` drives the HTTP
      boundary. Mutations <N-before> → <N-after>, all caught.
- [x] **`margin web`**, stdlib only, no new dependency, 127.0.0.1 only.
- [x] **The lossy stage reports what it took**, which nothing did before.

**Open, and not fixed by any of this:** the stored arm still fails its own bar
at 73/75, and the judge still shares a model family with the answerer. Both
unchanged and both still stated.

**Not done yet:** the PyPI upload. It happens after a hand-install of the built
wheel on this machine, so the first thing on PyPI is a version that has been
used rather than only built.
```

- [ ] **Step 6: Run everything, then commit**

```bash
source .venv/bin/activate && export PYTHONPATH="$PWD"
python src/property_test.py && python src/cli_test.py && python src/mcp_test.py \
  && python src/web_test.py && python src/package_test.py \
  && python src/eval_model.py --self-test && python src/mutation_test.py
for s in data/samples/*.json; do python src/eval_harness.py "$s" || break; done
git add -A && git commit -m "Docs: say what shipped, with every count re-measured

Rule 5 — the file counts, the gate count and the mutation count are read
off the repo here, not carried over. docs/status.md records that reading
list literals is how a check count stayed wrong at 77.

CLAUDE.md's packaging note is rewritten rather than deleted: it said the
src/ + tests split was a packaging benefit and nothing here was
packaged, which was true until packaging became the goal.

Rule 17 appended, not renumbered: a gate that runs inside the repo
cannot see the thing you ship.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9"
```

---

### Task 10: Use it, then the merge gate

**Files:** none — this task is verification and process.

- [ ] **Step 1: Install the built wheel by hand, as a stranger would**

```bash
cd "/Users/navyshukla/Margin Project" && rm -rf /tmp/margin-dist && uv build --wheel --out-dir /tmp/margin-dist
cd /tmp && uv tool install --force /tmp/margin-dist/*.whl
cd ~ && margin --help && margin web
```

Compress something real, use the page, copy out of it and paste it into a chat. **Step 2 does not begin until this has actually been done** — `docs/cli.md` exists because using the tool found what four green gates could not.

- [ ] **Step 2: Push and open the PR**

```bash
cd "/Users/navyshukla/Margin Project" && git push origin development
gh pr create --base main --head development \
  --title "Ship Margin: a pip-installable package, and a page that shows the saving" \
  --body "$(cat <<'BODY'
`pip install margin`, then `margin`, `margin web` or `margin mcp`.

The product's ten files are a package; the seventeen harness files stayed
put and import `margin.*`. `bin/margin` is gone.

Two new gates, because the eight existing ones all run inside this repo
and every one of them can pass while the shipped artifact is broken:
`src/package_test.py` builds a wheel and installs it into a clean venv,
`src/web_test.py` drives the HTTP boundary. Both run in CI, which means
both run on Linux while this is developed on macOS.

`margin web` is stdlib only — no new dependency, no build step, nothing
loaded from the network, 127.0.0.1 only and no `--host` flag.

`compress_json` now reports the keys its lossy first stage dropped,
which nothing did before. A CLI user had read the docs to get there;
someone who opens a page and clicks Copy has not.

Spec: `docs/superpowers/specs/2026-10-12-shipping-and-local-ui-design.md`
Plan: `docs/superpowers/plans/2026-10-12-shipping-and-local-ui.md`

Still open and unchanged by this: the stored arm's 73/75, and the judge
sharing a model family with the answerer.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RufBaZDYFULY5X2iZuqcy9
BODY
)"
```

- [ ] **Step 3: `/code-review` once, fix on `development`, merge**

Once, not until clean — the harness skill's merge gate. If a finding could have been a gate, the fix is the gate, not another round. Re-review only if the fixes were structural.

- [ ] **Step 4: PyPI, last**

```bash
cd "/Users/navyshukla/Margin Project" && uv build
uv publish   # needs a PyPI token
```

If PyPI refuses `margin` as too close to an existing name, change **only** `[project].name` to `margin-json`. The import package and the command stay `margin`, and `README.md`'s install line becomes `pip install margin-json`.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: packaging → 1–2, the UI → 7–8, the shipping gate → 3–4, `margin mcp` → 5, dropped-key reporting → 6, the hooks/workflow/mutation-path list → 1 (plus 3, 7, 8 as gates are added), docs → 9, order of work and success criteria → 10.

**One spec correction, carried into this plan:** the spec says mutations go 31 → 35. It did not account for `bin/margin`'s mutation retiring with the file. The real path is 31 − 1 + 4 = 34 after Task 4, then 35 (Task 5), 36 (Task 6), 39 (Task 8). Task 9 reads the final number off the file rather than trusting this paragraph.

**Placeholders:** none. The one deliberate defect is the corrupted `--warn` dark value in Task 8 Step 1, flagged immediately below the block with its correct value — left in to make the implementer read the CSS rather than paste it.

**Type consistency:** `compress_payload` returns the same six keys everywhere it is named (Task 7's interface block, `web.py`, `web_test.py`, `index.html`). `bind(port=DEFAULT_PORT)` is called with no argument in `main()` and with one in the fallback check. `LAZY_COMMANDS` is introduced in Task 5 and extended in Task 7, not redefined.

**Review Focus coverage:** all five have named checks in `src/web_test.py` (Task 7 Step 1) — taken port, 8 concurrent requests, over-cap body, non-UTF-8 bytes, missing `Content-Length`/unknown path — and three of the five additionally have mutations in Task 8 Step 6 proving those checks can fail.
