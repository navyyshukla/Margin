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
| github_issues.json | 31,023 | 23,413 | **24.5%** |
| hn_stories.json | 43,578 | 35,476 | **18.6%** |

Gated at 10% — comfortably under the worse of the two, so a differently-shaped
payload still gets the win, while a payload where the table barely helps falls
back rather than paying the cost of a second format for nothing.

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

## Flatten depth = 1 level (`FLATTEN_SEPARATOR`, src/table.py)

One level of nesting is lifted into dotted columns (`user.login`), deeper
nesting stays whole in a JSON cell.

Depth 1 covers every nested object in both payloads. It also does real work:
flattening **before** extracting constants finds 21 constant columns on GitHub
instead of 12, because `reactions.laugh` and `user.site_admin` are only visible
as columns once the parent dict is opened up — worth 12.2% on its own.

Each extra level multiplies column count, so depth 2 stays unbuilt until a
payload actually needs it.

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
