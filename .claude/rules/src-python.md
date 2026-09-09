---
paths:
  - "src/**/*.py"
  - "bin/margin"
---

# Writing code in this repo

These load only when a file under `src/` or `bin/margin` is read, because that is
the only time they matter.

- **Keep functions small and named for what they compute, not how.** `token_count`,
  not `helper1`.
- **No premature abstractions.** Three similar lines beat a speculative helper.
- **Every threshold or magic number gets a one-line comment saying where it came
  from** — measured, not guessed — and its derivation goes in
  `docs/thresholds.md`. Measure against the file as fetched, not `json.dumps`
  output, which pads with `", "` and `": "` that were never on disk.
- **All input is read in `src/cli.py` and nowhere else.** `compress.py` used to
  parse argv too, which made `margin` and `python src/compress.py` two
  implementations of one job, free to drift.
- **Use `table.same_json`, never `==`, to compare JSON.** Python says `True == 1`
  and `0 == 0.0`, so a document that decoded ints as floats compares equal and
  ships. Four places depend on this; any one left on `==` is a hole in the same
  wall.
- **Where two functions are inverses, they must key off the same source of
  truth** — which is why the writer and reader both live in `src/render.py`.

Rule numbers in comments (`Rule 4`, `Rule 9`, …) refer to `docs/harness.md`.
**20 of them are in these files** — 10 in `cli_test.py` alone — out of 27 across
the repo (measured 2026-09-10), so **do not renumber them**; new rules append.

Before changing a test, a threshold or the document format, load the **`harness`
skill** — it holds the checklist for what a change like that owes.
