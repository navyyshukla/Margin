# Thresholds, and where each number came from

Every number here was measured on this project's own sample payloads. None was
copied from Headroom — their figures come from different data and a different
compressor, and one of them would actively harm this one (see MIN_TABLE_SAVING).

Measured 2026-09-07 against:
- `data/samples/github_issues.json` — 30 open issues, facebook/react, 50,031 tokens raw
- `data/samples/hn_stories.json` — 30 top stories, HackerNews via Algolia, 43,578 tokens raw

Token counts are tiktoken `cl100k_base` throughout.

## `MIN_TABLE_SAVING = 0.10` (src/compress.py)

The table must beat plain JSON by at least 10% or the compressor emits JSON instead.

| Payload | stripped JSON | table | saving |
|---|---|---|---|
| github_issues.json | 31,023 | 23,420 | **24.5%** |
| hn_stories.json | 43,578 | 20,319 | **53.4%** |

Gated at 10% — comfortably under the worse of the two, so a differently-shaped
payload still gets the win, while a payload where the table barely helps falls
back rather than paying the cost of a second format for nothing.

HN read 18.6% here until 2026-09-08; scalar-array columns and depth-2
flattening took it to 53.4%. The threshold stays at 10% regardless: it exists to
catch the payload these rules do *not* suit, and raising it to hug whatever the
current best result happens to be would only reject that payload's smaller but
still real win.

**A warning about this table's left column.** "Stripped JSON" is
`json.dumps(...)`, whose `", "` and `": "` separators are not in the file as
fetched. On HN, which strips to nothing, the file itself is 35,585 tokens while
`json.dumps` of the same data is 43,578 — so the old "18.6% saving" was 18.6%
against a form of the data that never existed on disk. Measured against the
actual file it was **0.3%**. The gate must keep comparing against `json.dumps`,
because that is the fallback it would emit instead, but every figure quoted to a
human should be against the raw file:

| Payload | raw file | compressed | saving |
|---|---|---|---|
| github_issues.json | 50,031 | 23,420 | **53.2%** |
| hn_stories.json | 35,585 | 20,319 | **42.9%** |

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

## Long-text handling: currently inline (not yet a threshold)

The plan anticipated hoisting long strings out of CSV cells into
length-delimited blocks. Measurement says not yet:

| | chars |
|---|---|
| non-body string fields | 3 – 134 |
| `body` fields | 495 – 6,987 |

The gap between 134 and 495 is wide and empty, so a threshold anywhere in it
would be equally defensible — which is exactly why it shouldn't be picked yet.
Inline CSV already round-trips correctly on all 30 bodies (30 contain newlines,
14 contain quotes, 29 contain commas), so hoisting would be a token
optimisation, not a correctness fix. Build it when a measurement shows the
quoting overhead actually costs something worth the extra format complexity.
