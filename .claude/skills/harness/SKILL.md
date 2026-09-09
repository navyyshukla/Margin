---
name: harness
description: Margin's test-harness rulebook and the development→main merge gate. Use before editing src/*.py, bin/margin, any test file, or before merging to main.
---

# The harness

Six gates and thirteen rules. This file is the **procedure** — what to do. The
**record** — the failure that bought each rule — is `docs/harness.md`, and it is
cited by number from 27 places outside itself, 20 of them comments in `src/*.py`
(measured 2026-09-10). Read the rule
there before changing anything it governs. Never restate a rule here in a form
that could drift from it.

| Gate | Asks | Needs a sample payload? |
|---|---|---|
| `src/property_test.py` | Does the format survive shapes nobody wrote down? | no — generates its own |
| `src/cli_test.py` | Does the tool keep the promises the tool makes? | no — generates its own |
| `src/mutation_test.py` | Can the other gates still fail? | no — mutates the source |
| `src/eval_harness.py` | Do the answers survive on the payloads I have? | yes |
| `.claude/hooks/run_eval.sh` | Did Claude's last edit break any of them? | no |
| `.githooks/pre-commit` | Is this commit allowed to exist? | no |

Run all four by hand with `source .venv/bin/activate` then
`python src/{property_test,cli_test,mutation_test}.py`; `eval_harness.py` takes a
payload path, so loop it over `data/samples/*.json`.

---

## Before adding a format element

A "format element" is a new header line (`#dict`, `#store`) or a new cell
encoding (`dints`, a handle). Every one of these is a claim the document makes to
a reader, and claims are what the automated gates are worst at.

1. **Price both encodings on real payloads.** Never a ratio, never a size
   heuristic — `docs/thresholds.md` records `#dict` being decided this way and
   why the obvious distinct-count heuristic is wrong in *both* directions.
   Price with `render.encode_cell`, the encoder that actually writes the
   document, not with `json.dumps`; over-charging the status quo makes every new
   encoding look better than it is (Rule 4's shape).
2. **Add a `MUST_TABULATE` case** in `property_test.py`, not a `MAY_DEGRADE` one,
   unless the format genuinely cannot express the input (Rule 6). Make it big
   enough to clear `MIN_TABLE_SAVING` — a small case tests the fallback instead
   (Rule 6's corollary).
3. **Assert the element was *used*.** `compress_json` falls back to plain JSON
   when its own round-trip fails, so a completely broken encoder passes a naive
   round-trip check. Check the notes (Rule 1).
4. **Aim a check at the claim, not the data.** If the element asserts something
   round-trip cannot see — a type the column has no evidence for, which array was
   chosen, that a handle resolves — it needs its own assertion (Rules 3 and 7).
5. **Add a named mutation** to `mutation_test.py`, and watch the check *named for
   it* go red. Not the suite — the named check (Rule 13). A missing anchor is a
   failure, not a skip, and the baseline must be green first.
6. **Run a cold read.** Mandatory for a new header line or cell encoding — that
   is precisely what a reader cannot infer and no gate can see. Procedure and
   scoreboard: `docs/cold-reads.md` (Rule 10).

## Before writing a skip

Ask which gate runs in the environment that triggers the skip. If the answer is
"the one I am writing this for", it is not a skip — `pre-commit` runs the staged
tree out of a scratch directory with no `.venv`, so "skip when there's no venv"
was the only branch it could ever take. Find the correct behaviour for that
situation and check *that* instead (Rule 12).

## Before quoting a number in prose or a comment

Re-measure it. Quote against the file as fetched, not `json.dumps` output, which
pads with `", "` and `": "` that were never on disk. And when a measurement looks
wrong, check whether the *thing being measured* is wrong before writing a caveat
about how to read the number — a caveat is how a bug gets documented instead of
fixed (Rule 5).

## The merge gate

`pre-commit` refuses commits on `main` outright; the review is the other half.
For every merge to `main`:

1. Open the PR from `development`.
2. Run `/code-review` **once**.
3. Fix what it finds **on `development`**.
4. Merge.

**Once, not until clean.** PR #2 ran three rounds at ~80,000 tokens each because
each round re-reviewed the previous round's fixes; six of nine findings were the
same mistake, and `mutation_test.py` now catches all six in four seconds.
Re-review only when the fixes were *structural*.

Review is for what a gate cannot see — a design that is wrong rather than broken,
a claim in a doc, a risk nobody encoded. **If a finding could have been a gate,
the fix is the gate, not another round.**

Never merge on a green harness alone. Every stage so far has had at least one
real defect that only a reader found.

## After editing a hook

Re-run `./.githooks/install.sh`. Hooks are **copied** into `.git/hooks`, not
symlinked, so an edit to `.githooks/pre-commit` does nothing until you do. The
reason it is a copy rather than `core.hooksPath` is in a comment at the top of
`install.sh`.

---

## The thirteen rules, one line each

Full text and the failure behind each: `docs/harness.md`.

1. A test of a format must assert the format was **used** — the fallback
   round-trips trivially.
2. A generator that never triggers the code path proves nothing; a sweep that can
   silently test nothing must report its own coverage.
3. Round-trip equality cannot see a lie in the **header**. What the document
   claims needs a check aimed at the claim.
4. Encoder and decoder must dispatch on the **same** thing.
5. Every number in a comment must be re-measured, not remembered.
6. A test case must declare what should happen — `MUST_TABULATE` vs
   `MAY_DEGRADE` — not just "it worked".
7. Round-trip cannot see **which** records were chosen; pin the answer.
8. Unfinished is not the same as broken — a payload with no questions exits 3.
9. `==` is not equality for JSON (`True == 1`, `0 == 0.0`). Use
   `table.same_json` **everywhere**.
10. Some things only a cold reader can check. Re-run on every new format element.
11. The everyday invocation is a test case — argv, streams and exit codes only
    exist at the process boundary, so test the CLI as a subprocess.
12. A skip is a hole in whichever environment the gate actually runs in. A check
    aimed *near* the claim is not a check aimed at it.
13. Automate the mutation — intending to run it does not work. The mutation must
    name the gate that guards it, and the baseline must be green first.
