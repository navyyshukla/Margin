# Thresholds, and where each number came from

Every number here was measured on this project's own sample payloads. None was
copied from Headroom — their figures come from different data and a different
compressor, and one of them would actively harm this one (see MIN_TABLE_SAVING).

Measured 2026-09-07 against:
- `data/samples/github_issues.json` — 30 open issues, facebook/react, 50,031 tokens raw
- `data/samples/hn_stories.json` — 30 top stories, HackerNews via Algolia, 43,578 tokens raw

Token counts are tiktoken `cl100k_base` throughout.

## `MIN_TABLE_SAVING = 0.05` (src/compress.py)

The table must beat the JSON fallback by at least 5% or the compressor emits
that JSON instead.

Re-derived 2026-09-08 across eight payloads, table vs. the **compact** JSON that
is its actual alternative:

| Payload | compact JSON | table | saving |
|---|---|---|---|
| hn_stories.json | 35,585 | 20,284 | **43.0%** |
| coingecko_prices.json | 1,226 | 851 | **30.6%** |
| graphql_countries.json | 15,187 | 11,547 | **24.0%** |
| github_issues.json | 28,884 | 23,447 | **18.8%** |
| jsonplaceholder_posts.json | 7,162 | 6,437 | **10.1%** |
| pokeapi_ditto.json | 7,897 | 7,442 | **5.8%** |
| exchangerates_usd.json | 1,420 | — | no table |
| openmeteo_forecast.json | 3,642 | — | no table |

The smallest shipped saving is 5.8%, which is close enough to the 0.05 gate to
be worth watching: another readability line charged to every payload would push
`pokeapi_ditto.json` under it, and it would fall back to JSON in silence apart
from its note. Re-check this table whenever anything is added to every document.

**This number was wrong twice, in ways worth remembering.**

*First, the baseline was padded.* "Stripped JSON" used to mean
`json.dumps(...)` with its default `", "` and `": "` — whitespace never present
in the file as fetched. This document spotted that on 2026-09-08 and filed it as
a reporting caveat, saying the gate "must keep comparing against `json.dumps`,
because that is the fallback it would emit instead."

That was the wrong conclusion. The right one was that the *fallback* should not
have been padded. It was output, not a footnote — and on three of the six
payloads added later it made the compressor return **more** tokens than it was
given. `COMPACT` separators everywhere fixed the output and the denominator at
once. Every "table beats JSON by N%" figure recorded before that date was
measured against a competitor nobody would have shipped.

*Second, 0.10 was set on two payloads and cost a real win on the eighth.*
`pokeapi_ditto.json` compresses by a correct, verified 6.2% — 490 tokens — and
0.10 threw it away to avoid "the risk of a second format for nothing". That risk
was priced when the format was unproven. It is now self-describing (`#legend`)
and has passed a cold read by a model with no access to this repo, so the price
has dropped and the gate follows it.

0.05 keeps every real win in the sample set with room beneath the smallest, and
still refuses a table that merely breaks even. Deliberately **not** Headroom's
0.30, which would reject all but one of these.

### The stronger guarantee underneath the gate

`compress_json` also takes the original text and refuses to return anything
longer than it. A file's own formatting can tokenize slightly better than any
canonical re-serialisation, so "we could not improve this" has to mean handing
back exactly what arrived — Open-Meteo was still −0.1% on re-encoding alone.

Worst case across all eight payloads is now 0.0%. See `docs/shapes.md` for the
full table.

**Headroom uses 0.30 here.** Adopting it would reject both of our results — the
better one by 5.5 points. That is the concrete reason CLAUDE.md says to measure
rather than copy: their threshold is correct for their compressor and wrong for
this one.

## `MIN_ROWS_TO_TABULATE = 2` (src/compress.py)

The table's fixed cost is one header line, so it can only pay off once a key
name would otherwise be repeated — which is at the second row. Below that a
table is arithmetically incapable of winning, so it isn't attempted.

Same value Headroom uses, arrived at independently; it follows from the format
having exactly one fixed-cost line, not from their measurements.

## `MAX_FLATTEN_DEPTH = 2` (src/table.py)

Nesting is lifted into dotted columns (`user.login`); anything deeper than two
levels stays whole in a JSON cell.

Flattening **before** extracting constants is where the value is, at every
level: constants cannot see a column until the parent dict is opened. On GitHub
depth 1 found 21 constant columns instead of 12 (`reactions.laugh`,
`user.site_admin`) — worth 12.2% on its own.

This said "depth 1, and depth 2 stays unbuilt until a payload actually needs
it". hn_stories.json needed it: Algolia nests `_highlightResult.title` as its
own `{matchLevel, matchedWords, value}` dict, so at depth 1 the whole thing sat
in one JSON cell — 3,352 tokens, 16% of all cell content. Opened up,
`matchLevel` and `matchedWords` are identical in all 30 rows and collapse into
`#const`. Worth 1,731 tokens (7.9%), and free on GitHub, which has nothing at
that depth.

Still not unlimited: each level multiplies the column count, and a deep object
that varies per row would give a wide sparse table costing more than the JSON
cell it replaced. Raise it when a payload shows it pays.

## Scalar-array column types: `ints`, `dints`, `strs` (src/table.py)

An array of scalars in a CSV cell pays twice — once for JSON's `", "`
separators, again for CSV's quote doubling — and neither carries information.
Measured 2026-09-08 on `hn_stories.json`'s `children` column (arrays of comment
IDs), which alone was 81% of all cell content in the payload:

| encoding | tokens |
|---|---|
| JSON in a CSV cell | 33,310 |
| space-separated | 26,618 |
| first value, then differences | **13,525** |

`dints` is chosen when every array in the column is non-decreasing — the
structural signature of an ID or timestamp list, where neighbours are close and
the differences are small integers replacing 8-digit ones. Delta form is exactly
reversible for *any* int list (decoding is a running sum), so sortedness is a
token test, not a correctness one; an unsorted column keeps `ints`, where
deltas could be larger than the values they replace.

Each type has an admission test and a column failing one stays `json`, so an
unrepresentable column costs a saving and never a value.

## The `#legend` line (src/render.py)

45 tokens on `hn_stories.json`, 7 on `github_issues.json`, and the only entry
that truly earns its place is `dints`: a delta-encoded ID list renders as
`16582146 6 3 2`, and a reader taking those at face value answers with comment
IDs that do not exist. A saving that makes the model confidently wrong is worth
less than no saving at all, so the encodings that cannot be guessed are stated
in the document. Entries appear only when the document uses them.

## Not built: cross-column mirroring

Measured 2026-09-08, and rejected on the constraint rather than the arithmetic.
On HN, `_highlightResult.title.value` is identical to `title` in all 30 rows,
and the same holds for `author` and `url` — 912 tokens, 4.5%, fully reversible.

It was not built because collapsing them means the header says
`title = _highlightResult.title.value` and the model has to resolve an
indirection to read a title. The rule is lossless in bytes and lossy in
readability, and readability is the constraint this whole phase is held to. The
GitHub measurement argues the same way from the other side: its apparent
duplicates are sparse all-zero columns agreeing by coincidence, worth ~60
tokens — a rule that fires on accidents in a 30-row sample.

Revisit only with a payload where a mirrored column is large *and* obviously
derived.

## `MAX_RECORD_SEARCH_DEPTH = 4` (src/table.py)

How deep to hunt for the records. Was effectively 1 — top level, then one level
into a dict — which returned *nothing* for `{"data": {"items": [...]}}`. That is
JSON:API, GraphQL, and countless REST wrappers, and it compressed by exactly 0%.

4 covers every wrapper convention in the sample set with room to spare
(`graphql_countries.json` needs 2, the deepest seen). The walk only descends
through dict keys, so its cost is the number of nested objects, not the size of
the data. List *indices* are deliberately not walked: `#path` is a sequence of
keys, so a list inside another list could not be addressed on the way back.

Candidates are ranked by total cells, ties breaking toward the shallower path
then alphabetically. "First match" was an accident of dict ordering — a payload
whose small incidental list came before its real records tabulated the wrong
one, which round-trips perfectly and compresses almost nothing.

## `MIN_RECORDS_IN_MAP = 2` (src/table.py)

Same reasoning as `MIN_ROWS_TO_TABULATE`: below two records there is no repeated
key name to factor out. The interesting constraint on record maps is not the
count but the *value type* — see `docs/shapes.md`.

## Long-text hoisting: measured, and not built

**Closed 2026-09-08.** The plan anticipated hoisting long strings out of CSV
cells into length-delimited blocks. The measurement that was owed:

| | tokens |
|---|---|
| all 30 `body` fields, raw | 19,220 |
| the same fields as CSV cells | 19,336 |

**116 tokens, 0.6%** — that is the entire cost of CSV quoting on the payload
with the most prose in the sample set, across 30 bodies of which 30 contain
newlines, 14 contain quotes and 29 contain commas. Python's `csv` module was
already doing this efficiently.

Not worth a second block format and a length-delimited parser. The `body` wall
is real, but it is 82% of GitHub's output as *content*, not as quoting overhead,
and no rule compresses prose losslessly.
