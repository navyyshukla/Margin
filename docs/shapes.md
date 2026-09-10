# What shapes Margin handles, and what it does with each

Measured 2026-09-09 across eight payloads from eight different APIs. Every
number here is tokens under `cl100k_base`, measured against **the file as
fetched** — not against `json.dumps` output, which pads with `", "` and `": "`
that were never on disk (see `docs/thresholds.md`).

| Payload | shape | raw | out | saved | was |
|---|---|---:|---:|---:|---:|
| `github_issues.json` | bare list of records | 50,031 | 22,372 | **55.3%** | 53.1% |
| `hn_stories.json` | records under `hits` | 35,585 | 20,284 | **43.0%** | 43.0% |
| `graphql_countries.json` | records under `data.countries` | 13,011 | 8,530 | **34.4%** | 11.3% |
| `coingecko_prices.json` | record **map** | 1,226 | 851 | **30.6%** | 30.6% |
| `jsonplaceholder_posts.json` | 100 flat records | 8,761 | 6,462 | **26.2%** | 26.5% |
| `pokeapi_ditto.json` | one deep object | 7,897 | 7,465 | **5.5%** | 5.8% |
| `openmeteo_forecast.json` | already columnar | 3,638 | 3,638 | 0.0% | 0.0% |
| `exchangerates_usd.json` | map of scalars | 1,420 | 1,420 | 0.0% | 0.0% |
| **total** | | **121,569** | **71,022** | **41.6%** | 38.3% |

The `was` column is 2026-09-08, before `#dict`. Two payloads gained from it; the
other six were already free of repeated values worth factoring out.

Four payloads lost a little to legibility on the same day, and deliberately: the
`#dict` line is keyed by index rather than being a bare list, and the legend now
explains the repeating header. Together **+251 tokens, 0.3%** — bought by cold
read #3, which miscounted a hand-counted total and could not verify an index 61
deep into an unmarked array. `docs/cold-reads/2026-09-09.md` has the reasoning;
`HEADER_REPEAT_EVERY` made the same trade at the same price.

**Nothing comes out larger than it went in.** That is enforced, not hoped for:
`compress_json` takes the original text and refuses to return anything longer.
Three payloads used to violate it — Open-Meteo by 19%, exchange rates by 25%,
CoinGecko by 16% — because the JSON fallback was written with `json.dumps`
defaults.

---

## Handled

### Bare list of records — `[{...}, {...}]`
The original case. GitHub returns issues this way.

### Records under a key, at any depth — `{"hits": [...]}`, `{"data": {"items": [...]}}`
Found by a bounded walk (depth 4, dict keys only). `#path` records where they
were and `#wrap` carries the rest of the document, so the records go back
exactly where they came from.

Until 2026-09-08 the search stopped at depth 1, so `{"data": {"items": [...]}}`
— JSON:API, GraphQL, and countless REST wrappers — compressed by **exactly 0%**.

### Several record arrays in one document
The largest is tabulated (by total cells, ties breaking toward the shallower
path then alphabetically). The others stay as JSON inside `#wrap`.

Partial, deliberately. Multiple tables in one document would need a section
marker and a second header, and no payload in the sample set yet shows a second
array worth the format complexity. Build it when one does.

### Columns drawn from a small set — `#dict`
The gap `#const` left. That line states a value repeated in *every* row; this
one states the handful a column actually draws from, and the cells become
indices into it. `continent.name` across 250 countries is seven strings;
`author_association` across 30 issues is three.

Added 2026-09-09, worth 4,044 tokens net across the sample set. GitHub 53.1% →
55.3%, GraphQL countries 11.3% → **34.4%**.

Chosen by pricing both encodings, never by counting distinct values — see
`docs/thresholds.md`. The ratio heuristic is wrong in both directions here:
`languages` is 126 distinct across 250 rows and the biggest win in the set,
while `capital` is 245 distinct across 250 and the biggest loss.

### Record maps — `{"bitcoin": {...}, "ethereum": {...}}`
A dict whose values are all objects sharing one set of keys is a table whose
first column is the dict key. `#keyed` names that column; decompression turns
the rows back into a dict.

Common in price feeds, config APIs and anything Firebase-shaped. CoinGecko went
0% → 30.6%.

---

## Deliberately not helped

These are correct outcomes, not gaps. Each has eval questions asserting the data
survives untouched, because "we left it alone" still has to mean "we left it
alone *intact*".

### A single object — `GET /issue/123`
`pokeapi_ditto.json`, and the most common API shape there is. No key name is
repeated in a single object, so there is nothing for a table to factor out; a
one-row table costs a header plus a row and cannot win.

What it does get is boilerplate stripping, plus a table over whatever incidental
array is largest. On Ditto that is `game_indices` (46 rows), worth 5.8%. The
other 90% of the document rides along in `#wrap`, which is why that payload's
eval questions deliberately ask about fields *outside* the tabulated array.

### Already-columnar payloads — `{"hourly": {"time": [...], "temp": [...]}}`
Open-Meteo ships four parallel arrays of 168 scalars, each key written once.
That is essentially what this compressor produces, so there is nothing left to
factor out. No list of objects exists anywhere, so it emits JSON with a note.

A rule that started reshaping this would be inventing work. Its nine eval checks
exist to catch one that tried and lost data doing it.

### A map of scalars — `{"rates": {"EUR": 0.86, "JPY": 154.4}}`
The same dict-of-dynamic-keys shape as CoinGecko, but each value is one number
rather than a record. A two-column key/value table costs **1.3% more** than the
JSON it would replace: there is no repeated key name to factor out.

`exchangerates_usd.json` is in the sample set specifically to hold this line —
it is the payload that stops the record-map rule from over-reaching.

### Ragged objects under dynamic keys
`{"a": {"x": 1}, "b": {"y": 2, "z": 3}}` is a dict of unrelated things, not a
table. Identical key sets are required, so this is left alone rather than
tabulated into a wide sparse mess.

---

## Cannot be expressed, and degrades to JSON

The table is abandoned and plain JSON emitted, with a note saying why. Data is
never at risk — these are compression losses, not correctness ones.

| Case | Why |
|---|---|
| A key containing `,` or a newline | The header is one line of comma-separated `name:type` specs |
| A key containing `.` | Flattening claims every dot it sees, so the split back is ambiguous |
| The same key as both a value and a parent (`{"a": 5, "a.b": 6}`) | Cannot be a scalar and a dict at once |
| A cell containing a line identical to the header | The header repeats every 40 rows and is stripped by exact match, so the two cannot be told apart |
| Anything whose round-trip does not verify | The standing rule: if the table cannot be proved correct, emit JSON |

---

## The wall, and why it is not a shape problem

On `github_issues.json`, **82% of the remaining output is `body` prose**. On
`hn_stories.json`, 67% is `children` — an array of comment IDs, already
delta-encoded from 33,310 tokens to 13,525.

No rule compresses English losslessly.

**This section previously read "structure is close to exhausted… roughly 7%
headroom on GitHub". That was wrong, and `#dict` is the counterexample:** it
found 4,044 tokens the day after, 2,075 of them on GitHub. The claim was never
measured — it was inferred from prose being the largest remaining share, which
says what the biggest slice is and nothing about whether the rest is optimal.
Rule 5 covers exactly this: a number in prose gets re-measured, not remembered.

What is measurable now is where the tokens sit — **measured in document terms**,
which is the cost that actually ships:

| Payload | bulk content | share of its output |
|---|---:|---:|
| `jsonplaceholder_posts` | 5,743 | **89%** |
| `github_issues` | 19,669 | **88%** |
| `hn_stories` | 15,172 | **75%** |
| `graphql_countries` | 1,750 | 21% |
| `pokeapi_ditto`, `coingecko_prices` | 0 | 0% |
| **whole set** | **42,334** | **60%** of 71,022 |

"Bulk" means free-text fields plus `children` — content a question is answered
*from*, not *with*, and which no lossless rule reduces.

**An earlier version of this section put the figure at "82% of one payload, near
zero in five others" and concluded the store "targets a problem seven of eight
payloads do not have". That was wrong twice over**, and both mistakes came from
measuring prose rather than bulk:

- It counted only strings over 200 characters, which missed
  `jsonplaceholder_posts` entirely — 89% of that payload is short bodies.
- It excluded `children` because comment IDs are not prose. They are 13,525
  tokens, **19% of the entire sample set's output**, and they are exactly the
  kind of content the store exists to keep out of a prompt.

Rule 5 again, on a number that was one day old: the shape of the measurement
decided the answer, and the shape was chosen to match a hypothesis instead of
the thing being measured.

Going further means not putting bulk content in the prompt at all — a reversible
store the model can query — which turns Margin from a text filter into a tool
the model calls. That is a product decision and it reverses "no proxy server,
not yet". The corrected census is the case *for* it: 60% of what is left, across
four of eight payloads, is content no rule here will ever compress.

---

## The store, measured and built (2026-09-10)

A cell costing more than `MIN_STORE_SAVING` tokens is replaced by a `\@0001`
handle and its content moves to a store on disk. Built in PR #5; the CLI does not
use it yet.

| Payload | out now | with store | stored |
|---|---:|---:|---:|
| `github_issues.json` | 22,372 | **3,147 — 93.7%** | 33 cells |
| `hn_stories.json` | 20,284 | **3,887 — 89.1%** | 52 cells |
| `jsonplaceholder_posts.json` | 6,462 | **2,166 — 75.3%** | 100 cells |
| the other five | | unchanged, byte for byte | 0 |
| **total** | **71,022** | **31,104 — 41.6% → 74.4%** | |

**Three of eight payloads, and nothing at all for five.** Worth saying in that
order: the set total is driven entirely by the three, and overstating this is the
mistake recorded above. The five it does not help lose nothing — a document with
no qualifying cell carries no `#store` line and is byte-identical to what the
same payload produced before the store existed.

The 33 cells on GitHub are 30 issue bodies plus three long strings; the 52 on HN
are mostly `children`. That is the bulk census above, arrived at from a different
direction and without classifying anything as "bulk" — every cell was priced
against a handle and these are the ones that won.

### What the existing rules take first

The store is measured **after** `#const` and `#dict`, so it never claims a saving
they already took — and inside a single document that turns out to be a hard
boundary. A bulk value repeated in every row is factored out by `#const`; a few
repeated ones are claimed by `#dict`. Both get there before the store sees a cell.

So the store's content addressing does not dedupe within a document — there is
nothing left to dedupe. It dedupes **across** documents, which is where the same
issue body fetched on two different days actually costs something.
