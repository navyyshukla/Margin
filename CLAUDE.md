# Margin

A personal tool that compresses JSON API responses before they go into an LLM prompt, without
degrading answer quality on a fixed set of test questions. Inspired by Headroom
(https://github.com/headroomlabs-ai/headroom) but built from scratch, solo, rule-based first.

## Read these before acting

- **Changing `src/*.py`, `bin/margin`, a test, a threshold or a hook — or merging to `main`?**
  Load the **`harness` skill** first. It holds the checklist, the merge gate, and the sixteen
  rules; `docs/harness.md` holds the failure that bought each one.
- **`docs/README.md`** is the index — which doc answers which question.
- **`docs/status.md`** is what is done and what is next. Status is deliberately not kept in this
  file: it is re-read every session, so a stale claim here is one the model acts on. That has
  already happened once (this file claimed structure was exhausted; `#dict` disproved it the next
  day, unmeasured).

## Decisions already made (don't re-litigate without a real reason)

- **Language:** Python. Every free tool needed (tiktoken, tree-sitter, etc.) is first-class here.
- **Run mode:** a plain script/CLI on this machine. **The store stage reverses this deliberately**
  — a tool the model calls is the whole point of it — so treat the reversal as decided, not as a
  violation, and see `docs/status.md`.
- **Compression approach:** rule-based (if/regex/counting) first. No ML model until rules hit a real
  ceiling on real data.
- **Reversibility:** originals are always kept. Never delete-and-hope.
- **Thresholds:** measured against my own data and my own eval questions, never copied from
  Headroom's numbers. Every threshold or magic number gets a one-line comment saying where it came
  from — measured, not guessed — and `docs/thresholds.md` carries the derivation.
- **Scope:** one job — compress JSON API responses — done well, before adding a second trick.

## Git workflow (solo project)

- Day-to-day work happens on **`development`**, never `main`. The pre-commit hook refuses commits
  on `main` outright.
- Every merge to `main`: open the PR, run `/code-review` **once**, fix what it finds on
  `development`, then merge. Never merge on a green harness alone. Details and the "once, not until
  clean" reasoning are in the `harness` skill.
- Commit small, with messages describing *why*, and push `development` after each meaningful step.
- Pushing to GitHub is mandatory for this project — don't leave work only local.

## Conventions

- This machine's `python3` is PEP-668 locked (uv-managed); use `uv venv .venv` +
  `uv pip install -r requirements.txt`, not plain `pip`, to set up the environment
- Day-to-day use is `margin` (`curl ... | margin | pbcopy`); the scripts still run directly
  (after `source .venv/bin/activate`): `python src/<script>.py <args>`
- Code conventions for `src/*.py` and `bin/margin` live in `.claude/rules/src-python.md`, which
  loads itself when one of those files is read

## Layout

- `bin/margin` — the entry point; symlink it into `~/.local/bin` once (see `docs/cli.md`)
- `src/` — flat, one file per concern, **27 files**. Ten are the product (the pipeline —
  `compress`, `decompress`, `detect`, `render`, `store`, `table`, `tokens` — plus `cli.py`,
  `mcp_server.py`, `store_commands.py`); the other seventeen are the harness that guards it —
  the gates, the eval harness and its per-payload `checks_*.py`, the model-in-the-loop eval,
  and three measurement scripts. Deliberately not
  split into `src/` + `tests/`: every benefit of that split is a *packaging* benefit and
  nothing here is packaged, while the move would rewrite ten harness files — and a mutation
  tester that stops finding its targets fails green
- `docs/` — see `docs/README.md`
- `data/samples/` — real JSON payloads used for testing (gitignored by default — see `.gitignore`);
  `scripts/fetch_samples.sh` refetches all eight, and is the only place the source URLs are written
- `scripts/` — things that are neither product nor gate. One file so far
- `.github/workflows/gates.yml` — the five sample-free gates, on every push
- `requirements.txt` — kept minimal; add a dependency only when a line of code actually needs it.
  **Pinned exactly** — a floating `tiktoken` would move every token count in `docs/`

## Harness

Eight gates: `src/property_test.py`, `src/cli_test.py`, `src/mcp_test.py`, `src/mutation_test.py`,
`src/eval_harness.py`, `src/eval_model.py --self-test`, the `.claude/hooks/run_eval.sh` PostToolUse
hook, and `.githooks/pre-commit`. What each asks, and
what you owe before changing the format, is in the `harness` skill.

They do not all run at the same cadence: an edit to `src/*.py` runs five of them,
and a commit runs all six. `mutation_test.py` is the commit-only one — the skill's
table says why.

A **push** runs five of them again in GitHub Actions (`.github/workflows/gates.yml`) — the
five that need no sample payload. `eval_harness.py` is deliberately not among them and the
workflow says why; it is not a weaker gate, it is a gate with an input CI does not have.

The one thing you cannot discover from the repo: **run `./.githooks/install.sh` once per clone, and
again after editing any hook** — hooks are copied into `.git/hooks`, not symlinked, so an edit does
nothing until you re-run it. Why a copy and not `core.hooksPath` is commented at the top of
`install.sh`.
