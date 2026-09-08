# Margin

*the room your context earns back*

A personal, from-scratch project inspired by [Headroom](https://github.com/headroomlabs-ai/headroom):
a small tool that shrinks the JSON API responses I paste into LLM conversations, without breaking
the answers I get back. Built as a learning project — rule-based first, reversible always, one
feature at a time. See `CLAUDE.md` for the current architecture and decisions.

**Status:** the JSON compressor works and is tested against eight different APIs.
**38.5% overall**, and nothing ever comes out larger than it went in.

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
python src/compress.py response.json > compressed.txt
```

## What it does

Turns repetitive JSON into a compact, self-describing table, and can turn it
back. Field names are written once instead of once per record, anything
identical across every record is stated once in a preamble, and arrays of IDs
are stored as differences rather than full numbers.

```
#margin/v1
#legend \N = null; empty cell = key absent in that row
#const{"state":"open","locked":false}
[3]{number:int,title:str}
37537,Fix pragmas
37536,Fix inspect
37535,Fix errors
```

Measured across eight APIs (tokens under `cl100k_base`, against the file as fetched):

| Payload | shape | saved |
|---|---|---:|
| GitHub issues | bare list of records | **53.2%** |
| HackerNews (Algolia) | records under `hits` | **43.0%** |
| CoinGecko prices | record map | **32.1%** |
| JSONPlaceholder posts | 100 flat records | **26.8%** |
| GraphQL countries | records under `data.countries` | **12.5%** |
| PokéAPI (one Pokémon) | single deep object | **6.2%** |
| Open-Meteo forecast | already columnar | 0.0% |
| Exchange rates | map of scalars | 0.0% |

`docs/shapes.md` explains what each shape is and why the last two are correct
outcomes rather than gaps.

## The rules it holds itself to

- **Reversible.** `decompress(compress(x))` is verified at runtime, and a
  transform that cannot be undone is not shipped.
- **Never worse.** If nothing helps, the input comes back unchanged.
- **Answers must not move.** 76 questions across the eight payloads are asked
  before and after compression; any drift fails the build.
- **Readable by a model, not just by a parser.** The document explains its own
  encodings, and that claim is checked by giving it to a model with no access to
  this repo — see `docs/cold-read-2026-09-08.md`.
- **Every number measured, never copied.** Including from Headroom, whose
  thresholds would reject most of the results above (`docs/thresholds.md`).

## Layout

| Path | What |
|---|---|
| `src/compress.py` | the pipeline and its thresholds |
| `src/table.py` | decides the table's shape; renders no text |
| `src/render.py` | writes the document and reads it back, in one file so the two cannot drift |
| `src/decompress.py` | the inverse |
| `src/eval_harness.py` | asks the questions before and after |
| `src/property_test.py` | generates payloads trying to break the format |
| `docs/harness.md` | the six rules the test suite enforces, and the failure that bought each |
| `docs/shapes.md` | which API shapes are handled, and what happens to each |
| `docs/thresholds.md` | every threshold and where its number came from |

Run `./.githooks/install.sh` once per clone. `data/samples/` is gitignored — it
holds real API responses.
