# Margin

## What this is
A personal tool that compresses JSON API responses before they go into an LLM prompt, without
degrading answer quality on a fixed set of test questions. Inspired by Headroom
(https://github.com/headroomlabs-ai/headroom) but built from scratch, solo, rule-based first.
Background reading: the Headroom teardown and the "Before You Build" study/decision artifacts from
an earlier session (linked from project memory / prior chat — not duplicated here).

## Decisions already made (don't re-litigate without a real reason)
- **Language:** Python. Every free tool needed (tiktoken, tree-sitter, etc.) is first-class here.
- **Run mode:** a plain script/CLI on this machine. No proxy server, no hosted API, not yet.
- **Compression approach:** rule-based (if/regex/counting) first. No ML model until rules hit a real
  ceiling on real data.
- **Reversibility:** originals are always kept. Never delete-and-hope.
- **Thresholds:** measured against my own data and my own eval questions, never copied from
  Headroom's numbers.
- **Scope:** one job — compress JSON API responses — done well, before adding a second trick.

## Layout
- `src/` — the actual code, one file per concern as it's written
- `data/samples/` — real JSON payloads used for testing (gitignored by default — see `.gitignore`)
- `requirements.txt` — kept minimal; add a dependency only when a line of code actually needs it

## Conventions
- This machine's `python3` is PEP-668 locked (uv-managed); use `uv venv .venv` +
  `uv pip install -r requirements.txt`, not plain `pip`, to set up the environment
- Run scripts directly (after `source .venv/bin/activate`): `python src/<script>.py <args>`
- Keep functions small and named for what they compute, not how (`token_count`, not `helper1`)
- No premature abstractions — three similar lines beat a speculative helper
- Every threshold or magic number gets a one-line comment saying where it came from (measured, not
  guessed)

## Git workflow (solo project, no reviewer yet)
- Day-to-day work happens on **`development`**, not `main`
- `main` stays clean; only receives merges once a chunk of work is solid
- Commit small, with messages describing *why*, and push `development` after each meaningful step
- No PR-review ceremony yet — that's a deliberate future addition once there's real code worth
  reviewing (tracked as a TODO below, not built speculatively)
- Pushing to GitHub is mandatory for this project — don't leave work only local

## docs/
Doesn't exist yet. Add a doc file the first time there's a real, non-obvious decision to record
(e.g. a tuned threshold, a format-detection edge case) — not before.

## Harness (enforced, not just documented)
Run `./.githooks/install.sh` once per clone — it copies the hooks into `.git/hooks`. Don't set
`core.hooksPath` to `.githooks`: hooks are working-tree files, so on a branch that predates them
(like `main`) the file is absent and nothing runs — which defeats the hook whose job is guarding
`main`. Re-run `install.sh` after editing a hook.

- **`.githooks/pre-commit`** — refuses commits on `main` outright; refuses any commit staging
  `src/*.py` while `src/property_test.py` or `src/eval_harness.py` fails. The harness skips
  cleanly when `.venv` or the gitignored sample payload is missing (a fresh checkout has neither;
  that's not a regression); the property test generates its own payloads, so it always runs.
- **`.claude/hooks/run_eval.sh`** (wired in `.claude/settings.json`) — runs both after any Claude
  edit to `src/*.py` and exits 2 on failure, so a regression surfaces mid-session.

**`docs/harness.md` is the rulebook — read it before changing the format.** Ten rules, each
recorded with the failure that bought it. The three that catch people repeatedly:

- A test of the format must assert the format was *used*. `compress_json` falls back to plain JSON
  when its own round-trip fails, so a completely broken encoder still passes a naive round-trip
  check. Both tests were worthless until they checked the notes.
- Round-trip equality cannot see a lie in the *header*, or *which* array was chosen. What the
  document claims to the reader needs a check aimed at the claim, not at the data.
- `==` is not equality for JSON. Python says `True == 1` and `0 == 0.0`, so a document that decoded
  ints as floats compared equal and shipped. Use `table.same_json` everywhere.

Re-run the cold read whenever a **new cell encoding or format line** is added — that is precisely
what a reader cannot infer and no automated gate can see. Two runs so far,
`docs/cold-read-2026-09-08.md` and `-08b.md`; both scored every answer correct and both still found
a real defect.

## The `development` → `main` gate (exercised, not just described)
- [x] **PR-review gate.** The pre-commit hook blocks direct commits to `main`; the review step is
      the other half. First run: PR #1, `/code-review` before merging, findings addressed in the
      branch rather than after the fact.
- The standing procedure for every merge to `main`: open the PR, run `/code-review` on it, fix what
  it finds on `development`, then merge. Never merge on the strength of a green harness alone —
  every stage so far has had at least one real defect that only a reader found, whether that reader
  was the code reviewer or the cold-read model.

## Where things stand
The compressor is done and merged (PR #1). Eight payloads from eight APIs, 121,569 → 75,066 tokens
(**38.3%**), worst case 0.0% — nothing ever comes out larger than it went in. Per-payload numbers
and the shapes behind them are in `docs/shapes.md`.

## Next phase: make it usable (decided 2026-09-09)
Nothing consumes the compressor. It is `python src/compress.py f.json > out.txt`, and the result
gets opened, copied and pasted by hand — while the stated purpose is "shrinks the JSON API
responses I paste into LLM conversations". The library is proven; the tool around it does not exist.

Roughly: read stdin as well as a path, write to stdout cleanly (notes already go to stderr, so
`curl ... | margin | pbcopy` should just work), and a `margin` entry point that does not require
knowing where the repo lives. Small, and it reverses no decision.

Deliberately **before** the big fork, not after: using the thing daily is how you find out what it
actually needs, and the next decision is expensive to unmake.

## The fork after that (do not start it without deciding)
82% of `github_issues.json`'s output is `body` prose, which no rule compresses losslessly. Going
further means a reversible store the model can query — which turns Margin from a text filter into a
**tool the model calls**, reversing "no proxy server, not yet" above. Worth ~92% on GitHub.

Decide it against the eight payloads in `docs/shapes.md`, not the two that existed when it was
first raised, and decide it after actually using the CLI.

## Open TODOs
- [ ] Nothing harness-level.
