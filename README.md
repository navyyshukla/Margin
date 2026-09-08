# Margin

*the room your context earns back*

A personal, from-scratch project inspired by [Headroom](https://github.com/headroomlabs-ai/headroom):
a small tool that shrinks the JSON API responses I paste into LLM conversations, without breaking
the answers I get back. Built as a learning project — rule-based first, reversible always, one
feature at a time. See `CLAUDE.md` for the current architecture and decisions.

**Status:** the JSON compressor works and is tested against eight different APIs.
**41.8% overall**, and nothing ever comes out larger than it went in.

```bash
uv venv .venv && uv pip install -r requirements.txt   # once per clone
ln -s "$PWD/bin/margin" ~/.local/bin/margin           # once per machine
```

```bash
curl -s https://api.github.com/repos/python/cpython/issues | margin | pbcopy
margin response.json > compressed.txt
```

Reads a file or stdin. The document goes to stdout and everything else to
stderr, so it drops into a pipeline without putting notes on your clipboard.
Exit codes and the edge cases — not JSON, nothing to read, nothing worth
compressing — are in `docs/cli.md`.

## What it does

Turns repetitive JSON into a compact, self-describing table, and can turn it
back. Field names are written once instead of once per record, anything
identical across every record is stated once in a preamble, a column drawn from
a handful of repeated values is listed once and indexed, and arrays of IDs are
stored as differences rather than full numbers.

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
| GitHub issues | bare list of records | **55.4%** |
| HackerNews (Algolia) | records under `hits` | **43.0%** |
| GraphQL countries | records under `data.countries` | **35.7%** |
| CoinGecko prices | record map | **30.6%** |
| JSONPlaceholder posts | 100 flat records | **26.5%** |
| PokéAPI (one Pokémon) | single deep object | **5.8%** |
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
| `bin/margin` | the entry point; finds the repo from its own path |
| `src/cli.py` | arguments, streams and exit codes — the only place input is read |
| `src/compress.py` | the pipeline and its thresholds |
| `src/table.py` | decides the table's shape; renders no text |
| `src/tokens.py` | counting tokens, in one place |
| `src/render.py` | writes the document and reads it back, in one file so the two cannot drift |
| `src/decompress.py` | the inverse |
| `src/eval_harness.py` | asks the questions before and after |
| `src/property_test.py` | generates payloads trying to break the format |
| `src/cli_test.py` | runs the CLI as a subprocess and checks what it promises |
| `src/mutation_test.py` | breaks the CLI on purpose and checks that cli_test.py notices |
| `docs/harness.md` | the thirteen rules the test suite enforces, and the failure that bought each |
| `docs/cli.md` | exit codes, and the two properties the pipe depends on |
| `docs/shapes.md` | which API shapes are handled, and what happens to each |
| `docs/thresholds.md` | every threshold and where its number came from |

Run `./.githooks/install.sh` once per clone. `data/samples/` is gitignored — it
holds real API responses.
