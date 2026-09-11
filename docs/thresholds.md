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
| coingecko_prices.json | 1,226 | 851 | **30.6%**¹ |
| graphql_countries.json | 15,187 | 11,547 | **24.0%** |
| github_issues.json | 28,884 | 23,447 | **18.8%** |
| jsonplaceholder_posts.json | 7,162 | 6,437 | **10.1%** |
| pokeapi_ditto.json | 7,897 | 7,442 | **5.8%** |
| exchangerates_usd.json | 1,420 | — | no table |
| openmeteo_forecast.json | 3,642 | — | no table |

¹ Left as measured on 2026-09-08 rather than refreshed, because this table is
what *derived* the gate and a derivation is a dated thing. The figure today is
863 / **29.6%**: the `#keyed` legend clause grew 12 tokens on 2026-09-12 (see
*The `#legend` line* below). The derivation is unaffected — 29.6% clears 0.05 as
comfortably as 30.6% did.

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

## `MIN_DICT_SAVING = 20` (src/table.py)

Tokens saved, not a percentage and not a ratio of distinct values. A column
earns a `#dict` only if replacing its cells with indices beats writing them out
by at least this much.

Derived 2026-09-09 by pricing **every** candidate column across the eight
payloads — a candidate being any column with at least two distinct values and
at least one repeat. The results fall into two groups with nothing between them:

| built | | refused | |
|---|---:|---|---:|
| `graphql_countries.languages` | **+1369** | `pokeapi_ditto.game_index` | −8 |
| `github_issues.labels` | **+801** | `github_issues.reactions.+1` | −10 |
| `graphql_countries.continent.name` | **+95** | `hn_stories.author` | −55 |
| `github_issues.author_association` | **+25** | `graphql_countries.currency` | −291 |
| | | `graphql_countries.capital` | −544 |

Any threshold in `(-8, +25]` produces exactly these four dictionaries on this
data, so the precise value is not load-bearing. It is positive rather than zero
because a dictionary that merely breaks even still costs the reader a lookup,
and 20 rather than 25 to leave room under the smallest real win.

**Why tokens and not a percentage.** A dictionary's cost is nearly fixed — the
distinct values once, plus a couple of tokens of index per row — while its
saving scales with how long the values are. A percentage gate would accept a
column saving 4 tokens out of 8 and reject one saving 900 out of 4,000.

**Why not a distinct-count ratio**, which is the obvious heuristic and the one
I would have written without measuring. It is wrong in both directions on this
data: `languages` is 126 distinct across 250 rows — a ratio that looks hopeless
— and is the biggest win in the set, because each value is a long list of
objects. `capital` is 245 distinct across 250 and is the biggest loss. Only
pricing both encodings tells them apart.

**Keyed by index, not a bare list.** The `#dict` line is written as
`{"0": value, "1": value, ...}` rather than `[value, value, ...]`, costing +151
tokens across the sample set (+0.2%). Bought by cold read #3: asked for a value
at index 61, the reader had to hand-count 61 entries into an 11KB single-line
array, said there was "no way to verify an index", and on the next question
miscounted 58 as 57. Counting has been the weak point of all three cold reads.
`HEADER_REPEAT_EVERY` made the identical trade at +0.3% — see
`docs/cold-reads/2026-09-09.md`.

**One trap worth recording.** The first version of the cost model priced the
current cells with `json.dumps`, and a `str` cell is written **bare** in the
document — so `"x"` was charged three characters where the document holds one.
Over-charging the status quo makes every dictionary look better than it is, and
a column of `"x"`/`"y"` got one that cost more than the cells it replaced. Fixed
by pricing with `render.encode_cell`, the encoder that actually writes the
document. The general form is Rule 4: a gate has to price the output that will
really be produced, not a stand-in for it.

## `MIN_STORE_SAVING = 20` (src/store.py)

Tokens a single cell must save before its content moves out of the document and
into the store. Same unit and the same reasoning as `MIN_DICT_SAVING`, and
arrived at the same way — by pricing, never by a size heuristic.

Derived 2026-09-10 by `src/measure_store.py`, which **renders the whole sample
set at each candidate bar** rather than summing per-cell arithmetic:

| bar | cells stored | set total |
|---:|---:|---:|
| 1 | 257 | 73.3% |
| 5 | 209 | 73.2% |
| 10 | 187 | 73.1% |
| **20** | **166** | **72.8%** |
| 50 | 67 | 70.1% |
| 100 | 63 | 69.9% |

The curve is flat to 20 and falls away after it: 20 keeps 99% of the saving
while storing 91 fewer cells. Positive rather than zero for the reason
`MIN_DICT_SAVING` is — a cell that breaks even still costs the reader a *fetch*
to recover a value it could have read in place, and a fetch is far more expensive
to a reader than a dictionary lookup.

**The projection was checked against the real thing, and it had to be.** The
first version of `measure_store.py` summed the cost of cells priced one at a
time. Rendering the same documents disagreed by up to **86 tokens, 2.9% on
`jsonplaceholder`** — tiktoken is context dependent, so a cell priced alone does
not cost what it costs inside a CSV line — and it charged the fixed `#store` cost
to payloads that store nothing and carry no `#store` line at all. There is now
one path to a document size and no second one to disagree with it. Rule 1's
shape, on a measurement rather than an encoder.

**Which row to quote, because getting this wrong cost four files.** The
projection prices several handle encodings (two tables below). The one that
shipped is `\@0001[276t]` — a per-document id **carrying its token count**, which
is the `tokens only` row of the lure table: **73.5%**. The shipped CLI measures
**73.7%** (2026-09-11). Two tenths of a point apart, which is as close as a model
of a renderer gets.

The row that must *not* be quoted as the result is `per-document id @0001` in the
encoding table: **74.6%**, a bare handle with no `[Nt]`. Between 2026-09-10 and
2026-09-11 `src/store.py`, `docs/shapes.md` and `docs/status.md` all cited it, and
this file explained the residual gap as "the real `\@0001` being one character
longer than the `@0001` that was priced". That explanation was wrong. The gap is
the token-count lure, which the lure table below prices explicitly at 761 tokens
— it was measured, printed, and then read past.

Rule 5 says re-measure rather than remember. This is its sharper form: **a
multi-row projection needs the row named at every citation**, because re-reading
the wrong line is indistinguishable from re-measuring.

### `MAX_RESPONSE_TOKENS = 8000` (src/mcp_server.py)

What one `fetch` call may return. Measured 2026-09-10 against **every value the
sample set actually stores** — 171 of them, across the three payloads the store
helps:

| median | p90 | p95 | p99 | max |
|---:|---:|---:|---:|---:|
| 53 | 631 | 885 | 1,916 | 2,313 |

8,000 is ~3.5× the largest value this data produces, so no single fetch in the
sample set is ever truncated, while still admitting a dozen median values before
the cap bites.

It read "roughly a large issue body, times a few" until review pointed out that
this project measures its thresholds and that **this one has teeth**: it is what
makes an oversized value unreturnable if the surrounding code gets that case
wrong. Which it did — see below.

**The case the number made reachable.** The cap was applied as
`spent + cost > MAX_RESPONSE_TOKENS`, evaluated with `spent == 0` for the first
id. A single value larger than the whole cap was therefore dropped, with a
message telling the model to *"fetch them in a second call"* — advice that fails
identically every time, which is a loop rather than an error. A 15,000-token
issue body is exactly what `MIN_STORE_SAVING` sends to the store, so it was
reachable on real data. Now a value that cannot fit is returned **truncated and
labelled as a first part**, and only ids crowded out by *other* ids are deferred.

`QUERY_CONTEXT_CHARS = 240` is a legibility knob rather than a threshold, and is
chosen rather than measured — but its downside is bounded, not open:
`_spans_matching` hands back the whole value when the spans do not come to less
than it, so a too-generous window can never cost more than not querying at all.

### `HASH_WIDTH = 24` (src/store.py) — and it was 12, wrongly

96 bits, for both the content hash and the document id. **This was 12 (48 bits),
and the comment justifying it was wrong in a way worth keeping.**

It said "under 10⁻⁹ at the ~10⁴ distinct cells across the whole sample set". The
arithmetic was right and the **scope** was wrong. A sample set is not the
population: this store is one global namespace with no eviction that accumulates
for years, so n is total objects ever written, not cells in one run.

| objects | 48 bits | 96 bits |
|---:|---:|---:|
| 10⁴ | 1.8 × 10⁻⁷ | 6.3 × 10⁻²² |
| 10⁶ | **0.18%** | 6.3 × 10⁻¹⁸ |
| 10⁷ | **18%** | 6.3 × 10⁻¹⁶ |

Found by comparing against Headroom, which uses 96 bits here and documents it
against birthday bounds. Their store survives at any width because it evicts on a
30-minute TTL — that is what keeps their n small, and it is precisely the
difference the original comment failed to notice.

Widening costs **1 token per document**, 21 across the sample set, because only
the doc_id is written into the document; the content hash is not there at all,
which is what the id/index indirection bought. There was never anything to
economise against.

A doc_id collision is worse than an object collision, which is why both are wide:
two payloads sharing a doc_id means one document's handles resolve against the
other's index, and every handle then returns **plausible wrong content** rather
than failing.

Rule 5, third instance in this project, and the same shape as both the ones
`docs/shapes.md` records: a measurement whose scope was chosen to fit the
hypothesis.

### The handle's lure: `\@0001[142t]`

A bare `\@0001` tells a reader nothing. It cannot judge whether it wants the
value, so it either fetches every handle — which costs more than never having
stored them — or fetches none and answers from the columns around it. Headroom's
equivalent marker carries a description and a count for exactly this reason.

Measured across the sample set, every figure a real render:

| handle | set total | cost |
|---|---:|---:|
| bare `\@0001` | 74.0% | — |
| **`\@0001[142t]`** | **73.5%** | **0.5 pts** |
| `+ 32-char content preview` | 71.9% | 2.1 pts |
| `+ 48-char preview` | 71.1% | 2.9 pts |
| `+ 80-char preview` | 69.6% | 4.4 pts |

The token count is taken at 0.5 points. **The content preview is not taken yet**,
at four times the price — whether a reader needs it is what cold read #4 is for,
and this format has twice paid for legibility *after* a read demonstrated the
need (`HEADER_REPEAT_EVERY`, the keyed `#dict` line) rather than on suspicion.

**The first version of this comparison was wrong**, and in the usual way: the
"bare" baseline row was accidentally priced with the 12-hex sample rather than
the handle actually shipped, which made `\@0001[142t]` look **761 tokens
cheaper** than the thing it costs more than. A baseline that is not what it
claims to be is Rule 5 wearing a different hat.

### The handle's own encoding, measured

**Bare handles, no `[Nt]` size** — a comparison *between encodings*, not a
prediction of the shipped result. The handle that shipped carries its token count
and measures 73.7%; read the warning above before quoting any figure here.

| encoding (bare) | example | set total |
|---|---|---:|
| hex-4 | `@3f9a` | 74.3% |
| hex-8 | `@3f9a2c1b` | 73.5% |
| hex-12 | `@3f9a2c1b7e4d` | 72.8% |
| hex-16 | `@3f9a2c1b7e4d5a6b` | 72.2% |
| **per-document id** | `@0001` | **74.6%** |

The column is internally consistent, and that is all it is for: it is what chose a
per-document id over a hash in the document. The 1.8 points below is a difference
between two rows of *this* table, so the correction above leaves it intact.

Putting the content hash straight in the document costs **1.8 points, ~2,200
tokens** against a short id. So the document carries a short per-document id and
the store keeps the `id -> hash` index **on disk**, where it never costs a prompt
token. Content addressing survives the indirection, which is the point: objects
still dedupe, and `get` still verifies that content hashes to its own name rather
than trusting the filename.

A shorter hash was the obvious compromise and is not safe: hex-8 is 32 bits, and
at ~10⁴ cells the birthday probability is over 1%. A collision serves one cell's
content for another's, which is data loss — the one outcome "never
delete-and-hope" rules out. 12 hex is 48 bits, under 10⁻⁹, and `put` refuses to
overwrite differing bytes anyway.

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

**Re-measured 2026-09-12, storeless documents: 80 tokens on
`graphql_countries.json`, 70 on `hn_stories.json`, 66 on `github_issues.json`,
32 on `coingecko_prices.json`.** This section said "45 and 7" until then, which
was true on 2026-09-08 and has been wrong ever since: the line has since grown
the repeating-header clause, the `col:dict` clause, the handle clause and now the
`#keyed` negation, each bought by a cold read. Rule 5 — and the particular trap
here is that the legend is the one part of the format whose *price rises every
time a reader is confused*, so a figure from before the last read is always low.

The only entry that truly earns its place on arithmetic is `dints`: a
delta-encoded ID list renders as `16582146 6 3 2`, and a reader taking those at
face value answers with comment IDs that do not exist. A saving that makes the
model confidently wrong is worth less than no saving at all, so the encodings
that cannot be guessed are stated in the document. Entries appear only when the
document uses them — `coingecko_prices` pays for `#keyed` and nothing else.

### What the `#keyed` clause costs, and why it grew (2026-09-12)

| wording | coingecko legend | payload out | saved |
|---|---:|---:|---:|
| `column _key holds each record's key` | 20 | 851 | 30.6% |
| **`… holds the key each record was stored under, and is NOT a field of the record`** | **32** | **863** | **29.6%** |

12 tokens, one point on the one payload of eight that is a record map, and
nothing set-wide (71,022 → 71,034; 41.6% either way). Bought by cold read #5,
which had the first wording in front of it and returned `_key` as a thirteenth
field of the `ethereum` record. The precedent is the one directly below on
`HEADER_REPEAT_EVERY` and the keyed `#dict` line: pay for legibility **after** a
read demonstrates the need, never on suspicion.

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
