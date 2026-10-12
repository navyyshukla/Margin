# Shipping Margin, and the local UI that comes with it

Design, 2026-10-12. Supersedes nothing; **reverses one decision on the record**
and says so below.

## Why this exists

`docs/status.md` says the project is finished, and it is — against the scope
`CLAUDE.md` set. This is a new scope, chosen deliberately: **a stranger should be
able to install Margin and see what it saves them.** Today they cannot do either.
Installing means cloning a repo and building a venv by hand, and the only way to
see a saving is to read a percentage on stderr.

Two things follow, and the second is the reason the first is worth doing:

1. **Packaging.** `pip install margin`, then `margin`. The name is free on PyPI
   as of 2026-10-12 (the JSON API returns 404 for it).
2. **A local UI.** `margin web` serves one page on `127.0.0.1` where you paste
   JSON and see the before/after and the percentage. It runs the real Python
   pipeline on the user's own machine.

## The decision this reverses, stated plainly

`bin/margin` lines 8–14 and `CLAUDE.md`'s layout note both decline packaging, for
a reason that was correct at the time: `src/` is flat modules importing each other
by bare name, so `[project.scripts]` would mean either restructuring every file
the harness guards, or installing `table`, `render` and `detect` into
site-packages under names anything could collide with.

That argument assumed nothing was packaged. **Once packaging is the goal the
argument inverts** — the restructuring is no longer a cost paid for nothing, it
is the thing being bought. The `src/` + harness split that `CLAUDE.md` called "a
packaging benefit and nothing here is packaged" now has something to be a benefit
to.

The old comments do not get quietly deleted. They get rewritten to say what was
true and what changed.

## What was considered and rejected

| Option | Why not |
|---|---|
| Public hosted web app | Costs money every month, needs rate limiting and abuse handling, and strangers' API payloads would transit a machine I own. |
| Static demo page on GitHub Pages | Free, but shows my eight payloads rather than the user's JSON. It sells the tool; it does not use it. |
| Compressor rewritten in JavaScript | A second implementation no gate guards, free to drift from the Python one. Exactly what `CLAUDE.md`'s scope rule exists to prevent. |
| FastAPI / uvicorn behind the page | Two pinned dependencies every user installs for a page they may never open. `requirements.txt`: add a dependency only when a line of code needs it. |
| React + Vite | A committed bundle the eight gates cannot read inside, and a build step before every release. |

## Layout after the change

The product becomes a package. **The harness does not move.**

```
margin/              the ten product files, a real package
  __init__.py
  cli.py  compress.py  decompress.py  detect.py  render.py
  store.py  store_commands.py  table.py  tokens.py  mcp_server.py
  web.py             NEW — the local server
  web/index.html     NEW — the whole UI, one file

src/                 the seventeen harness files, unchanged in purpose
  property_test.py  cli_test.py  mcp_test.py  mutation_test.py
  package_test.py    NEW — the gate that answers "does it work when shipped"
  eval_harness.py  eval_model.py  checks_*.py (8)  measure_*.py (3)

pyproject.toml       NEW
bin/margin           DELETED — the console script replaces it
```

**Why the harness stays in `src/`.** Only the ten product files need to be
importable from an installed wheel. The seventeen harness files are never
shipped, and moving them would rewrite seventeen files to buy nothing. After the
change they import the product by package name — `from margin.compress import
compress_json` — which is one mechanical edit per import line, not a
restructuring.

Internal imports inside `margin/` become **explicit relative** imports
(`from .table import MISSING`), so the package cannot accidentally pick up a
same-named module from the working directory.

## `margin web` — the UI

One screen. Paste JSON on the left, compressed document on the right, and
underneath: `50,031 → 3,298 tokens`, a bar, and `55.3% saved`, plus a Copy
button. No tabs, no settings, no store toggle, no stage breakdown. Scope was set
in conversation on 2026-10-12 and this is it.

**Server.** `http.server.ThreadingHTTPServer`, stdlib only, bound to
`127.0.0.1`. Three routes and nothing else:

| Route | Does |
|---|---|
| `GET /` | serves `web/index.html` |
| `POST /compress` | body is raw bytes; returns JSON `{document, before, after, percent, note, dropped}` |
| anything else | 404 |

**No `--host` flag.** Binding to `0.0.0.0` would put an unauthenticated endpoint
that parses untrusted JSON onto the local network. Nothing here needs it, and
`docs/cli.md`'s standing pattern is to decline flags that buy keystrokes.

**Port.** Tries `8731` first so the URL is stable enough to bookmark; falls back
to an ephemeral port if it is taken, and prints whichever it got. 8731 is
arbitrary and unassigned in the IANA registry — it is not a measured number and
the comment in the source will say exactly that rather than implying otherwise.

**`POST /compress` calls `compress_json` directly** — the same function `cli.py`
calls. Not a reimplementation and not a subprocess. There is one compressor, so
the eight existing gates still guard the only one that exists, and a pipeline
change reaches the UI for free.

### The one line of honesty

When the first pipeline stage actually drops keys, the page prints one sentence
under the output naming them:

> 4 link keys dropped and not recoverable: `html_url`, `node_id`, …

Shown only when the stage fired, and never otherwise. `CLAUDE.md`'s first rule is
reversibility and "never delete-and-hope". A CLI user had to read the docs to
arrive; someone who opens a GUI cold and clicks Copy did not, and silently eating
`html_url` behind a Copy button breaks that promise harder than the CLI does.
This requires `compress_json` to **report** what it dropped — it currently does
not, and nothing else reports it either (verified 2026-10-12: no "dropped" string
reaches `cli.py`).

### Error cases, and which are already decided

`cli.py` has settled these once; the UI must agree with it rather than invent a
second policy.

| Input | `/compress` does |
|---|---|
| not JSON | returns the bytes unchanged, `note` says so, HTTP 200 — matching the CLI's exit 0 |
| not UTF-8 | same, unchanged bytes, `note` says so |
| nothing worth compressing | unchanged bytes, `percent` 0.0 — a correct outcome, not an error |
| deeply nested (either recursion) | caught, unchanged bytes, `note` says compression failed. **Two guards, because `docs/cli.md` records that there are two recursions and one payload can only ever reach one of them** |
| empty body | HTTP 400 — unlike the CLI's exit 2, there is no argv to be wrong about |

No dialogs, no alerts, no network calls from the page. No CDN, no fonts, no
analytics: the page works on a plane.

## The gate that answers "does it work when shipped"

This is the part the existing harness cannot do. All eight gates run inside the
repo, against `src/*.py`, with a hand-built `.venv`. Every one can pass while the
shipped artifact is broken, because these failures only exist after a build:

| Breaks only when packaged | User sees |
|---|---|
| `web/index.html` missing from the wheel | `margin web` serves a 404 |
| a bare-name import survived the rename | `ModuleNotFoundError: table` |
| console script misdeclared | `margin: command not found` |
| `google-genai` left in the runtime deps | a Gemini SDK installed to compress JSON |
| something assumes `.venv/bin/python` | works here, dies on their machine |

Same class as the failure `docs/cli.md` already records twice: green gates that
had never seen an argument, a stream or an exit code (Rule 11). One layer further
out.

**`src/package_test.py`** — the fourth subprocess-driving gate, following the
pattern `cli_test.py` and `mcp_test.py` set:

1. `uv build` → a real wheel
2. a throwaway venv in a temp dir, with no path into this repo
3. `uv pip install <wheel>` and nothing else
4. then, using only that venv's `margin`:
   - `margin --help` exits 0
   - a small inline payload compresses, and `python -m margin.decompress` brings
     it back
   - `margin web` starts, `GET /` returns the real page, `POST /compress` returns
     the right counts
   - `margin mcp` starts and answers one JSON-RPC call
   - `margin fsck` exits 0 against an empty store
5. tear the temp dir down

**It needs no sample payload, so it runs in CI on every push** — which means it
runs on Linux while development happens on macOS. That is where the platform
assumptions get caught.

**It gets mutation coverage, or it is decoration.** Rule 14: a detector nobody
has shown a positive to is not a detector. New mutations, each naming the check
that must go red:

- remove `web/index.html` from the wheel's package data
- revert one import in `margin/` to a bare name
- break the `[project.scripts]` entry point
- move `google-genai` back into the runtime dependencies

Mutations 31 → 35. The baseline must be green first (Rule 13).

## `margin mcp` — a fifth subcommand, and why it clears the bar

Today a user points their MCP client at a path inside the repo. After packaging
there is no such path — the code is somewhere inside a tool venv they cannot
predict. `margin mcp` is the only way the server stays reachable.

This clears the bar `docs/cli.md` set when it refused `--decompress`: that flag
was a convenience for something `python src/decompress.py` already did, so
refusing it cost keystrokes. Refusing `margin mcp` costs the feature. Same test
the four store subcommands passed.

It also inherits the cost that document already records: dispatch on the first
word means a file literally named `mcp` or `web` is ambiguous. `margin ./web` and
`margin < web` still work, neither is a plausible payload filename, and this is
where it is written down.

## What else has to change, so nothing is found halfway

Every one of these is a path that currently says `src/`:

- **`.claude/hooks/run_eval.sh`** — its `case` matches `$PROJECT_DIR/src/*.py`
  and `$PROJECT_DIR/bin/margin`. The second path is being deleted; the first must
  also match `margin/*.py`, or editing the product stops running any gate. That
  is Rule 12's hole precisely, and this hook already carries a comment about
  having been a silent no-op once.
- **`.githooks/pre-commit`** — the `grep -E '^(src/.*\.py|bin/margin)$'` filter,
  and a subtler thing: it runs the **staged** tree out of a scratch directory. With
  the product in a package, `import margin` inside that scratch tree would resolve
  to whatever is installed on the machine rather than to the staged copy, so the
  gate would grade the wrong code while reporting green. **`PYTHONPATH` must point
  at the scratch root**, and a check must prove it — Rule 12 again.
- **`.github/workflows/gates.yml`** — five step paths, plus the new sixth step.
- **`src/mutation_test.py`** — it mutates source by path. Pointed at `src/` it
  would stop finding its targets, and a mutation tester that finds nothing passes
  green. This is the single highest-risk edit in the whole change.
- **`requirements.txt`** — `google-genai` moves to a dev extra. It is only used
  by `eval_model.py`, inside the function that calls Gemini.
- **`README.md`, `CLAUDE.md`, `docs/README.md`, `docs/cli.md`, `docs/harness.md`,
  `docs/status.md`** — install instructions, the layout tables, the "27 files"
  count, the gate count (8 → 9), the mutation count (31 → 35), and the two
  comments that decline packaging.
- **`./.githooks/install.sh`** must be re-run after the hook edits, or they do
  nothing (hooks are copied, not symlinked).

## Order of work

Packaging first, UI second. The UI is the easier half and the packaging is what
can go wrong, so the gate that proves shipping works should exist before there is
a UI riding on it.

1. `pyproject.toml`, `margin/` package, relative imports, console script,
   `bin/margin` deleted. Harness imports updated.
2. `src/package_test.py` + its four mutations. **Green before step 3.**
3. Hooks, workflow, `mutation_test.py` paths. Re-run `install.sh`.
4. `margin mcp`.
5. `compress_json` reports what it dropped.
6. `margin web` + `web/index.html`.
7. Docs: every count re-derived, not remembered (Rule 5).
8. Install the built wheel by hand on this machine, from the wheel and not the
   source tree, and use it.
9. PR → `/code-review` once → fix on `development` → merge.
10. PyPI upload last, after step 8 has actually been done.

## Success criteria

- `pip install margin` in a clean venv on a machine that has never seen this
  repo, then `margin f.json`, `margin web` and `margin mcp` all work.
- `src/package_test.py` is green, and red when any of its four mutations is
  applied.
- The eight existing gates are still green, with their counts re-measured rather
  than carried over.
- No new runtime dependency. `pip install margin` still installs `tiktoken` and
  `mcp` and nothing else.
- The UI's numbers match the CLI's for the same payload, because both call the
  same function.

## Open, and deliberately not resolved here

- **PyPI may refuse `margin` as too close to an existing name.** Only the first
  upload proves it. Fallback is the distribution name `margin-json`; the import
  package and the command stay `margin` either way.
- **The stored arm still fails its own bar at 73/75.** Unrelated to shipping and
  not fixed by it. It stays red and stays stated — `docs/status.md` is the record.
