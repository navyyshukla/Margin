# Cold read #2, 2026-09-08

Owed by `#keyed`, a new format line. Method and running score:
[`docs/cold-reads.md`](cold-reads.md).

Three payloads — CoinGecko (a record map), GraphQL countries (records two levels
down, nulls, emoji, nested objects), and PokéAPI Ditto (one deep object where
most of the data rides in `#wrap`).

## Result: 16 of 16 correct

Every factual question right, including the two that mattered most:

- **The record map reconstructed correctly.** Asked what shape the original data
  had, the reader said "an object keyed by coin id, not a list", quoted the
  `#keyed` legend line as its reason, and wrote out the original JSON structure
  accurately. That is the whole point of `#keyed`, confirmed by someone who had
  never seen it.
- **Nulls, nested objects and emoji all read correctly** — `\N` for a missing
  capital, `continent.name` unflattened back to a nested object, 🇮🇳 intact
  through CSV.

**Confidence: 7/10**, up from 6/10 on the first read.

## What it got wrong first, and the pattern that is now clear

The reader miscounted columns on the CoinGecko row: it treated `_key` as not a
column, shifted by one, and would have answered `gbp_market_cap`
(1,164,202,106,599) as bitcoin's USD price instead of 78,509. It caught this by
explicitly re-counting 13 header fields against 13 values.

**This is the same failure as the first cold read**, which drifted across a run
of empty cells and briefly concluded the wrong story had no url. Two reads, two
near-misses, both from counting positional values against a header some distance
above. Both caught by recounting — but "caught by recounting" is not a property
of the format, it is luck about how careful the reader was.

**Fixed:** the column header now repeats every 40 rows
(`HEADER_REPEAT_EVERY`). Measured at **+191 tokens across all eight payloads,
0.3%**, and only the three payloads with more than 40 rows pay anything at all.

## The other findings

Fixed, all free or near-free:

| Finding | Fix |
|---|---|
| `eur:json` for a column holding `67514` and `2138.02`. The reader said plainly it "couldn't tell you a principled reason" why that was `json` while `eur_24h_change` beside it was `float` | New `num` type for mixed numeric columns. Same bytes, honest label |
| `#keyed"_key"` — no delimiter between directive and value. Readable only because the legend explained it in prose first; alone it was "a bare string I couldn't confidently parse" | Now `#keyed "_key"` |
| `continent.name` — the dotted-path convention was the one thing the reader got right purely by recognising the notation from elsewhere. The document never said it | Legend entry, emitted whenever a column uses it |

Left alone, with reasons:

- **`#wrap` holds a tiny skeleton on one payload and the entire rest of the
  document on another**, with nothing marking the difference. A fair
  observation. It is the same thing semantically — the document minus the rows —
  and the difference is plain on reading the content.
- **CSV quoting is never announced.** The reader recognised the convention
  unaided, which is the argument for having chosen CSV in the first place.

## A test that was testing nothing

Worth recording. Repeating the header means the parser must strip it back out,
matched in full against whole lines — and a quoted cell may legally contain
newlines, so a cell whose text contains a line identical to the header is the
one input that defeats it.

The first version of that regression case used the same string in every row. The
constant-column rule lifted it into `#const`, where its newlines were
JSON-escaped and the cell never existed. The case passed while exercising
nothing. Making the value vary per row produced the real behaviour: the payload
falls back to JSON with a `ROUND-TRIP MISMATCH` note, data exact.

That is the correct outcome and it is accepted deliberately — the alternative is
giving up repeated headers, which two cold reads say are worth more than this
case costs. It lives in `MAY_DEGRADE`, not `MUST_TABULATE`.

When the read is owed again, and the running score:
[`docs/cold-reads.md`](cold-reads.md).
