# Eval questions — github_issues.json

Fixed test set for `data/samples/github_issues.json` (30 open issues, `facebook/react`,
fetched 2026-09-07). Answers are the ground truth from the raw payload, checked before any
compression is applied. Run the same question against the compressed payload; if the answer
drifts from ground truth, the compression rule that caused it is over-aggressive.

Question types are deliberately mixed — extraction, counting, filtering, aggregation — because
each stresses different fields, and a compression rule that only gets tested against one type
can quietly break the others.

## Extraction (single-field lookup)
1. What is the title of issue #37508?
   → `[DevTools Bug] Cannot remove node "1708" because no matching...`
2. Who opened issue #37501?
   → `dependabot[bot]`
3. What labels are on issue #37510?
   → `CLA Signed`, `dependencies`, `javascript`

## Counting / aggregation
4. How many of the 30 issues are pull requests (have a `pull_request` key) vs. plain issues?
   → 24 PRs, 6 plain issues
5. How many issues have zero comments?
   → 11
6. What is the total comment count across all 30 issues?
   → 31
7. How many distinct users opened these issues/PRs?
   → 21 distinct logins (`wapgear` appears 3x, `sleitor` and `wbinnssmith` 4x each,
     `dependabot[bot]` 2x)

## Filtering
8. Which issues are labeled `Type: Bug`?
   → #37534, #37508
9. Which issues were opened by `dependabot[bot]`?
   → #37510, #37501
10. List the issue numbers that are plain issues, not PRs (no `pull_request` key).
    → #37534, #37533, #37521, #37512, #37508, #37507

## Reasoning over content (needs `body` / `title` text, not just structure)
11. Summarize in one sentence what issue #37534 is about.
    → DevTools "Inspect" button doesn't work with elements in a top-layer (e.g. `<dialog>`,
      popover) context.
12. Which PR is a dependency bump, and what package does it touch?
    → #37510 bumps `fast-uri` (3.1.2 → 3.1.7); #37501 bumps `@humanfs/node` (0.16.6 → 0.16.8)

## Adversarial (should NOT be answerable — checks the model isn't hallucinating from noise)
13. What is the avatar URL of the user who opened issue #37537?
    → This field only exists to test whether stripping `*_url` fields causes a confident
      hallucination instead of "not available." A correct compressed-payload answer is
      "not present in this data," not a fabricated URL.

## How to run
For each question: paste the payload (full, then compressed) into a fresh conversation, ask the
question, compare the answer to the ground truth above. A rule "passes" only if all of 1–12 stay
correct after compression. Question 13 exists to catch hallucination, not to be answered.
