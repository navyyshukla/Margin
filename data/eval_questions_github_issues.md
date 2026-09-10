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

---

# Eval questions — hn_stories.json

Second payload: 30 top stories from HackerNews via Algolia
(`https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=30`), fetched 2026-09-07.

This one exists to answer a different question from the GitHub set. That set asks "does
compression preserve answers?"; this set asks **"do the rules generalize, or were they quietly
shaped around GitHub?"** So the questions deliberately lean on how this API differs:

- records live under `hits`, not at the top level
- `url` is the story itself, not a link template — the case that would break a name-based rule
- `_tags` is an array of plain scalars, a shape GitHub never produced
- one story has no `url` at all (a real null, not an absent key)
- the wrapper object carries its own metadata (`nbHits`, `page`) beside the records

Executable form: `src/checks_hn_stories.py`. Run with
`python src/eval_harness.py data/samples/hn_stories.json`.

## Preserve (answer must be identical after a compress/decompress round-trip)
1. Title of the top-scoring story → `Stephen Hawking has died` (6,015 points)
2. Author of story `16582136` → `Cogito`
3. **url of story `16582136`** → `http://www.bbc.com/news/uk-43396008` — the one that must survive
4. Story count → 30
5. Total points across all stories → 120,745
6. Total comments across all stories → 38,947
7. Distinct authors → 28 (`davidbarker` and `grey-area` appear twice each)
8. Stories with no url → `37392676` only
9. Most-discussed story → `CrowdStrike Update: Windows Bluescreen and Boot Loops` (3,859 comments)
10. `_tags` of story `16582136` → `["story", "author_Cogito", "story_16582136"]`
11. Wrapper metadata survives → `nbHits`, `hitsPerPage`, `page` all intact

## Removed
None. `strip_boilerplate` finds no `*_url` keys anywhere in this payload, so it removes nothing
at all — which is correct behaviour, not an oversight. A REMOVED check here would have to invent
something to delete.

## Manual (needs a human or an LLM)
12. Summarize what the top 3 stories are about
13. Which stories are about AI companies, and what happened in each


---

# The other six payloads

`github_issues.json` and `hn_stories.json` are written out in prose above because
they were the first two, and the prose is what the question set was designed
from. The six added on 2026-09-08 live only as code, in `src/checks_<name>.py`,
and that is deliberate rather than a backlog.

**The check module is the question set.** Prose and code drifted apart within a
day the first time — the prose still named `src/checks_hn.py` after the module
was renamed — and where they disagree the code is what actually runs. Writing
the questions once, executably, removes the chance to disagree.

Every module carries the same three lists, and each question's name says what it
guards:

| Payload | Module | What its questions are aimed at |
|---|---|---|
| `graphql_countries.json` | `checks_graphql_countries.py` | the `{"data": ...}` envelope surviving; null capitals; multi-codepoint emoji; a nested object and an array of objects, 250 rows of each |
| `jsonplaceholder_posts.json` | `checks_jsonplaceholder_posts.py` | 100 flat rows — ids staying contiguous 1..100, and no column being invented where every row has the same four keys |
| `pokeapi_ditto.json` | `checks_pokeapi_ditto.py` | fields **outside** the tabulated array, since only `game_indices` is tabulated and a wrapper bug would return those rows perfectly while dropping the other 90% |
| `openmeteo_forecast.json` | `checks_openmeteo_forecast.py` | the payload the compressor cannot help: 0% and completely intact, four parallel series staying the same length, units surviving |
| `coingecko_prices.json` | `checks_coingecko_prices.py` | a record map — the map keys are the records' identity, so losing them makes every row anonymous |
| `exchangerates_usd.json` | `checks_exchangerates_usd.py` | the boundary that stops the record-map rule over-reaching: 166 bare floats, where a key/value table costs more than it saves |

## How to run

```bash
for f in data/samples/*.json; do python src/eval_harness.py "$f"; done
```

Both hooks do this on every edit and every commit. A payload with no module yet
exits 3 and is reported as unguarded rather than failing the build — see
`docs/harness.md` Rule 8.

The `MANUAL_QUESTIONS` in each module are the ones no assertion covers: whether a
model can actually *read* the result. Those are answered by the cold read
(`docs/harness.md` Rule 9), not by the harness.
