# context-compressor

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

## Open TODOs (harness-level, not feature-level)
- [ ] Add a PR-review gate on merges from `development` → `main` once there's enough code to make
      review meaningful (see study plan Part D — this was deliberately deferred, not forgotten)
