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
deep into an unmarked array. `docs/cold-read-2026-09-09.md` has the reasoning;
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

What is measurable now is where the tokens sit. Free text as a share of each
payload's output:

| `github_issues` | 82% | `hn_stories` | 13% | `jsonplaceholder` | 8% | other five | 0% |
|---|---:|---|---:|---|---:|---|---:|

Going further means not putting full prose in the prompt at all — a reversible
store the model can query — which turns Margin from a text filter into a tool
the model calls. That is a product decision, deliberately deferred, and the
table above is why it stays deferred: **86% of all the prose in the sample set
is in one payload**, so the fork targets a problem seven of eight payloads do
not have. Decided against eight payloads rather than the two that existed when
it was first considered, which is what the previous version of this file asked
for.
