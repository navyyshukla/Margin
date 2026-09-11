# Margin

*the room your context earns back*

A personal, from-scratch project inspired by [Headroom](https://github.com/headroomlabs-ai/headroom):
a small tool that shrinks the JSON API responses I paste into LLM conversations, without breaking
the answers I get back. Built as a learning project — rule-based first, reversible always, one
feature at a time. See `CLAUDE.md` for the current architecture and decisions.

**Status: finished** (2026-09-12) — the scope in `CLAUDE.md` is met and measured;
`docs/status.md` says what is done, what stays open and what was declined.
The JSON compressor works and is tested against eight different APIs.
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

With `--store` in use, four subcommands keep that store recoverable — `margin
fsck`, `margin gc`, `margin export <doc-id> <file>`, `margin import <file>`.
See `docs/cli.md` for why these earned a place the narrow CLI refused
`--decompress`.

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
| CoinGecko prices | record map | **29.6%** |
| JSONPlaceholder posts | 100 flat records | **26.2%** |
| PokéAPI (one Pokémon) | single deep object | **5.5%** |
| Open-Meteo forecast | already columnar | 0.0% |
| Exchange rates | map of scalars | 0.0% |

`docs/shapes.md` explains what each shape is and why the last two are correct
outcomes rather than gaps.

Those figures are three stages, and the first one throws data away. Set-wide:
formatting 12%, dropping link-template keys 29%, the table format 59%. The
dropping stage fires on exactly one of the eight payloads — GitHub, the only API
here using the `*_url` convention — where it is the largest single contributor.
`docs/shapes.md` has the per-stage table and the list of what is gone for good.

## The rules it holds itself to

- **Reversible after one deliberate cut.** The first stage *drops* link-template
  and opaque-ID keys — `*_url`, `node_id`, `gravatar_id` — and nothing restores
  them, so a compressed GitHub payload cannot answer "link me to issue 37508".
  Everything after that stage is exactly reversible, `decompress(compress(x))` is
  verified at runtime, and a transform that cannot be undone is not shipped past
  that line. What goes and what it costs: `docs/shapes.md`.
- **Never worse.** If nothing helps, the input comes back unchanged.
- **Answers must not move, and a model was finally asked.** 80 checks assert the
  dropping stage kept every field a question needs; they cannot see the table
  format, because the round-trip check above them already proved it exact. So
  `src/eval_model.py` puts the documents to a reader that has never seen this
  repo: **raw 75/75, compressed 75/75, stored 73/75** across eight payloads, and
  the 16 comprehension questions are judged too — **16/16 on every arm**, by a
  judge that passed 16 of 16 controls including 8 it had to call *different*. The
  compressed arm is exact — `#keyed`'s `_key` column stopped being reported as
  data once the legend said outright that it is not a field. The stored arm's two
  misses are arithmetic on a column that holds no handle, they are the same two
  as the previous run, and the run fails its own bar because of them. The judge
  shares a model family with the answerer, which is the weaker instrument and is
  said so in `docs/cold-reads/2026-09-12-judge.md`. `docs/status.md` has the rest,
  including what that leaves unproven.
- **Readable by a model, not just by a parser.** The document explains its own
  encodings, and that claim is checked by giving it to a model with no access to
  this repo — seven times so far, scored in `docs/cold-reads.md`. Every one of
  the first five found a real defect no automated gate could see; the last two
  were run to check a fix and found none in the document.
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
| `src/store_commands.py` | `fsck`, `gc`, `export`, `import` — what keeps the store recoverable |
| `src/decompress.py` | the inverse |
| `src/eval_harness.py` | asks the questions before and after |
| `src/eval_model.py` | asks a *model* the questions — three arms, and not a gate |
| `src/property_test.py` | generates payloads trying to break the format |
| `src/cli_test.py` | runs the CLI as a subprocess and checks what it promises |
| `src/mcp_test.py` | drives the MCP server as a subprocess over JSON-RPC |
| `src/mutation_test.py` | breaks the CLI on purpose and checks that cli_test.py notices |
| `src/checks_<payload>.py` | the expected answers for one sample, paired to it by name (eight of them) |
| `src/measure_*.py` | one-off measurement scripts — not gates, nothing runs them but you (`measure_miscount.py` is the five-arm run that isolated the stored miscount) |
| `docs/` | the written record — `docs/README.md` is the index of which file answers what |

Run `./.githooks/install.sh` once per clone. `data/samples/` is gitignored — it
holds real API responses.
