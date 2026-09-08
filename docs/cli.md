# The CLI, and the four decisions it turns on

Built 2026-09-09. The compressor had been finished and merged for a day and
nothing consumed it: `python src/compress.py f.json > out.txt`, then open the
file and copy it by hand, while the stated purpose was "shrinks the JSON API
responses I paste into LLM conversations".

Everything here is shaped by one pipeline:

```
curl -s https://api.github.com/repos/python/cpython/issues | margin | pbcopy
```

## Install

```bash
uv venv .venv && uv pip install -r requirements.txt   # once per clone
ln -s "$PWD/bin/margin" ~/.local/bin/margin           # once per machine
```

`bin/margin` resolves symlinks to find the repo, so the link can live anywhere
and the repo can move. Not a packaged console script: `src/` is flat modules
importing each other by bare name, so `[project.scripts]` would mean either
restructuring every file the harness guards into a package with relative
imports, or installing `table`, `render` and `detect` as top-level
site-packages modules under names anything could collide with. The wrapper
costs ten lines and touches no import.

## Usage

```
margin f.json          margin < f.json          curl ... | margin
margin -               margin -q f.json         margin --help
```

With no path and stdin a terminal, it prints usage and exits rather than
blocking on the tty — a filter that waits silently for input nobody is typing
looks exactly like a hang.

## Exit codes

| Code | Means | Cases |
|---|---|---|
| 0 | stdout is what you want | compressed; 0% saving; not JSON; not even UTF-8; **compression crashed** |
| 2 | margin was called wrong, or there was nothing to read | no such file; empty input; two paths; unknown option |
| 141 | the reader closed the pipe (`\| head -1`) | normal, and what `cat` does |

**A crash is a 0, not a 1.** The compression call is wrapped, and anything that
escapes it emits the input unchanged with a loud line on stderr. The library's
standing rule is "if the table cannot be proved correct, emit JSON"; this is the
same rule one layer up — if the payload cannot be compressed at all, emit the
payload.

Bought by a real one. `json.loads` parses 3,000 nested arrays happily in its C
scanner, and then `strip_boilerplate` recurses through them and blows the stack.
`RecursionError` is not a `JSONDecodeError`, so it escaped every guard: exit 1,
a traceback, and **zero bytes on stdout** — `curl ... | margin | pbcopy`
replacing the clipboard with nothing. The `except` is deliberately `Exception`
rather than `RecursionError`: the promise is about the pipeline, not about which
bugs were anticipated.

There is deliberately **no code for "compressed poorly"**. 0% saving is a
correct outcome for `openmeteo_forecast.json` and `exchangerates_usd.json` —
see `docs/shapes.md` — not a failure, and a non-zero exit there would make
`margin f.json > out` look broken on payloads the compressor is right about.

**Not JSON also exits 0**, which is the least obvious call here. A failed curl
hands back an HTML error page; margin passes it through and says so on stderr.
The argument for a non-zero code is catching that in a script, and it does not
hold up: `pbcopy` runs regardless of what margin exited with, so a non-zero
buys no actual protection at the point of danger, while it does break `set -e`
for something that is not an error — margin returned exactly what it was given.
What protects you is the stderr line, so that line is loud and only `-q`
silences it.

## Two properties the tests are aimed at

### stdout carries the document and nothing else

Notes, warnings and the savings summary go to stderr. Not tidiness — anything
on stdout ends up on the clipboard. `src/cli_test.py` checks it by throwing
stderr away and decompressing stdout alone: a leaked note breaks the parse.

### "Unchanged" means byte-for-byte unchanged

Two paths hand the input back: a payload no rule improves, and input that is
not JSON. Both were approximate before — the JSON path added a trailing newline
the file never had, so on `openmeteo_forecast.json` a note reading "returned the
input unchanged" produced 5,603 bytes from a 5,602-byte file.

One byte, and worth fixing rather than rewording, for the reason Rule 5 gives:
when a measurement looks wrong, check whether the thing being measured is wrong
before writing a caveat about how to read it. A filter you can drop into an
arbitrary pipeline has to mean "unchanged" literally. So the CLI reads and
writes bytes at the boundary and emits exactly what it read — which also means
input that is not valid UTF-8 survives intact instead of crashing on the decode.

Compressed output still ends in exactly one `\n`.

## What using it immediately found

`margin f.json | head -1` — the way you look at a document — printed a
BrokenPipeError traceback under the output. Python installs its own SIGPIPE
handler and turns the signal into an exception; restoring `SIG_DFL` makes
margin die on the signal the way every other Unix filter does.

Four gates were green throughout. All four call `compress_json` in-process, so
none of them had ever seen an argument, a stream or an exit code. That is the
whole argument for building the tool before the next phase, and it is now
Rule 11 in `docs/harness.md`.

The PR review then found the same bug still live on the *other* entry point:
`python src/compress.py f.json | head -1` tracebacked, because its `__main__`
called `cli.main()` without `die_on_broken_pipe()`. A second entry point is a
second place to forget a line, so it now runs `cli.py` **as `__main__`** through
`runpy` — everything `margin` does, with no setup list to keep in sync.

## No summary line on the passthrough paths

`not JSON` and `not UTF-8` print what happened and stop. The percentage would be
`0.0%` by construction, and computing it is what drags tiktoken onto the one
path whose entire promise is handing your bytes back — on a machine with no
tiktoken cache, that means fetching a BPE vocabulary over the network to print a
constant, so a failed curl on a fresh laptop would traceback instead of passing
through.

## Deliberately not built

- **`--decompress`.** One flag away and the tool's own inverse, but scope says
  one job; `python src/decompress.py f.txt` covers the rare occasion.
- **Multiple files, `-o`.** `>` and `|` already do both.
- **A TTY check on the summary line.** Printing stats only when stderr is a
  terminal is tempting and is the same shape as the `jq` bug in
  `run_eval.sh`: behaviour keyed to invisible state, silently doing nothing.
  Always on, `-q` off.
