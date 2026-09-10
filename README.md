# Margin

*the room your context earns back*

A personal, from-scratch project inspired by [Headroom](https://github.com/headroomlabs-ai/headroom):
a small tool that shrinks the JSON API responses I paste into LLM conversations, without breaking
the answers I get back. Built as a learning project — rule-based first, reversible always, one
feature at a time. See `CLAUDE.md` for the current architecture and decisions.

**Status:** the JSON compressor works and is tested against eight different APIs.
**41.6% overall**, and nothing ever comes out larger than it went in. With
`--store` and the MCP server, **73.7%** — bulk values move to a content store the
model fetches from, and `docs/store.md` explains what that costs as well as what
it saves.

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
| GitHub issues | bare list of records | **55.3%** |
| HackerNews (Algolia) | records under `hits` | **43.0%** |
| GraphQL countries | records under `data.countries` | **34.4%** |
| CoinGecko prices | record map | **30.6%** |
| JSONPlaceholder posts | 100 flat records | **26.2%** |
| PokéAPI (one Pokémon) | single deep object | **5.5%** |
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
  this repo — four times so far, scored in `docs/cold-reads.md`.
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
| `src/detect.py` | decides whether the input is JSON at all |
| `src/render.py` | writes the document and reads it back, in one file so the two cannot drift |
| `src/store.py` | the content store: bulk cells live here, not in the prompt |
| `src/mcp_server.py` | the `fetch` tool — what makes Margin a tool the model calls |
| `src/decompress.py` | the inverse |
| `src/eval_harness.py` | asks the questions before and after |
| `src/property_test.py` | generates payloads trying to break the format |
| `src/cli_test.py` | runs the CLI as a subprocess and checks what it promises |
| `src/mcp_test.py` | drives the MCP server as a subprocess over JSON-RPC |
| `src/mutation_test.py` | breaks the CLI on purpose and checks that cli_test.py notices |
| `src/checks_<payload>.py` | the expected answers for one sample, paired to it by name (eight of them) |
| `src/measure_*.py` | one-off measurement scripts — not gates, nothing runs them but you |
| `docs/` | the written record — `docs/README.md` is the index of which file answers what |

Run `./.githooks/install.sh` once per clone. `data/samples/` is gitignored — it
holds real API responses.
