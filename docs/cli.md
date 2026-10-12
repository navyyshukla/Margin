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
pip install margin
```

**This section argued the opposite until 2026-10-12**, and the argument is kept
rather than deleted because it was right at the time. It read: not a packaged
console script, because `src/` is flat modules importing each other by bare name,
so `[project.scripts]` would mean either restructuring every file the harness
guards or installing `table`, `render` and `detect` as top-level site-packages
modules under names anything could collide with — while `bin/margin` cost ten
lines and touched no import.

Every clause of that was true. What changed is the premise underneath it: the
cost of restructuring is only a cost if nothing is packaged. Once `pip install
margin` is the goal, it is the thing being bought. And the restructuring turned
out to be smaller than feared — the ten product files moved into `margin/` with
relative imports, the seventeen harness files stayed in `src/` and import
`margin.*`, which is one line each.

`bin/margin` is gone. Its four `cli_test` checks are gone too, and
`src/package_test.py` holds what survives of them: "the command works from a
directory that is not the repo" is now a property of an install rather than of
our code, so it is checked against a built wheel in a throwaway venv. See
`docs/harness.md` Rule 17.

## Usage

```
margin f.json          margin < f.json          curl ... | margin
margin -               margin -q f.json         margin --help
margin --store f.json

margin fsck                      margin gc [--delete]
margin export <doc-id> <file>    margin import <file>
margin mcp                       margin web
```

`--store` moves bulk cells out of the document and into a content store, taking
`github_issues.json` from 55.3% to 93.4%. **It is off by default and that is the
design**: the output of the default pipeline gets pasted into a chat, and a chat
without this machine's MCP server has no fetch tool at all — the handles would be
dead and the values silently missing. See `docs/store.md`.

With a store in use the summary line stops saying "saved", because content moved
to disk is not content removed:

```
margin: 50,031 → 3,298 tokens (93.4% in the prompt)
margin: 32 value(s) held in the store — fetchable, not discarded
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

**A crash is a 0, not a 1.** Parsing and compression are both inside one guard,
and anything that escapes emits the input unchanged with a loud line on stderr.
The library's standing rule is "if the table cannot be proved correct, emit
JSON"; this is the same rule one layer up — if the payload cannot be compressed
at all, emit the payload.

Bought by a real one, and then bought again. 3,000 nested arrays parse fine in
`json.loads`' C scanner and then blow the stack in `strip_boilerplate`;
`RecursionError` is not a `JSONDecodeError`, so it escaped every guard — exit 1,
a traceback, and **zero bytes on stdout**, `curl ... | margin | pbcopy`
replacing the clipboard with nothing.

The first fix wrapped the compression call, which covered half the surface. At
10,000 `json.loads` raises `RecursionError` *itself*, from inside
`detect_content_type` — so the identical failure was still live one layer out
while this document already claimed it could not happen.

The second fix raised the test payload to 10,000 to catch that, and thereby
stopped exercising the first half: the pipeline short-circuits at whichever
recursion comes first, so a single payload can only ever reach one of them.
Moving the compression guard back out still passed every check. **There are two
recursions, so there are two guards and two payloads** — one at ~3× the
recursion limit that parses and then dies in `strip_boilerplate`, one at 10×
that dies in the parser — and the test asserts which half each one reaches
rather than trusting a number to keep landing in the right band.

The `except` is deliberately `Exception` rather than `RecursionError`: the
promise is about the pipeline, not about which bugs were anticipated.

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

## The four store subcommands, and why they are not the same call as `--decompress`

Added 2026-09-12, and they have to answer the scope rule that kept
`--decompress` out one section down. The answer is not "these are more useful".

**`--decompress` is a convenience for something another entry point already
does.** `python src/decompress.py f.txt` is right there. Nothing is lost by
refusing it but a few keystrokes.

**A store with no `fsck` and no bundle unit is unrecoverable by any other
means.** There is no second way to find an object no index points at — the leak
`docs/store.md` has named since the store shipped — and no second way to move a
document to another machine with the content it needs. Refusing these does not
cost keystrokes; it costs the data.

| | |
|---|---|
| `margin fsck` | every index against the objects it names. Exit 1 if content a document needs is gone, 0 if the only finding is disk |
| `margin gc [--delete]` | objects no index references. **Dry run by default** |
| `margin export <doc-id> <file>` | one document's index and objects, as a JSON bundle |
| `margin import <file>` | a bundle into this store, through `commit`, so a conflicting object still refuses |

**`gc` is the only irreversible thing in this project**, so it does nothing by
default and prints what it would remove. CLAUDE.md's first decision is that
originals are kept and never delete-and-hope; a sweep that ran on sight would be
exactly that.

**The cost of subcommands, stated rather than discovered.** `parse_args` treats
every bare word as a path, so dispatching on the first word makes a file literally
named `export` ambiguous. `margin ./export` and `margin < export` both still
work, and none of the four names is a plausible payload filename — but the
ambiguity is real and this is where it is written down. `margin f.json` is
byte-identical to what it always was; the dispatch happens before anything else
looks at argv.

**`margin mcp` (2026-10-12) is a fifth, and it clears the same bar.** Before
packaging, an MCP client was pointed at a path inside this repo. After
`pip install margin` there is no such path — the code sits in a tool venv nobody
can predict — so refusing this subcommand does not cost keystrokes, it costs the
feature. Same test the four above passed.

It also turned the ambiguity above from a stated cost into an observed one. The
first run of `src/package_test.py`'s new check invoked `margin mcp` with the
installed venv's `bin/` as its working directory — and the `mcp` package puts a
console script literally named `mcp` there. So margin read its own subcommand as
a filename, found a shell script, and reported `not JSON — passed through
unchanged`. That is the predicted failure, found by the gate written for the
feature, on the first attempt. `margin ./mcp` and `margin < mcp` still reach the
file.

The bundle is **one JSON file**, not an archive: stored objects are UTF-8 text by
construction, so a bundle is readable and diffable and there is no tar handling
to get wrong. It is not a `#margin/v1` format element — no new header line, no
new cell encoding — so per `docs/cold-reads.md` it owes no cold read.

## `margin web`, and the four decisions in it

Added 2026-10-12. The CLI prints a percentage on stderr, which is the right
output for a pipe and a poor way to *see* what this does. One page on
`127.0.0.1`: paste JSON on the left, document on the right, and underneath the
two token counts, a bar, the percentage and a Copy button. Nothing else — no
tabs, no settings, no `--store` toggle, no per-stage breakdown.

**It calls `compress_json`, the same function `cli.py` calls.** Not a copy, not a
subprocess, and above all not a reimplementation in JavaScript. A browser-side
compressor was the obvious way to avoid a server and it was rejected for the
reason CLAUDE.md's scope rule exists: it would be a second implementation that no
gate in this repo can see, free to drift from the Python one. There is one
compressor, so the gates that guard it guard the page, and
`src/web_test.py` asserts the page's document is byte-identical to the library's
— with a mutation that swaps in a second implementation, caught by that check.

**`127.0.0.1` only, and there is no `--host` flag.** `0.0.0.0` would put an
unauthenticated endpoint that parses untrusted JSON onto whatever network the
machine is on. Nothing here needs it, and this document's standing pattern is to
decline flags that buy keystrokes.

**The passthrough policy is not a second policy.** Not JSON, not UTF-8, and
too-deep-to-parse all behave the way the exit-code table above settled them: the
bytes come back, a note says why, and the status is a success. Two recursion
guards, for the reason this document already records — there are two recursions
and one payload can only ever reach one of them. The one unavoidable divergence
is non-UTF-8 input: the CLI hands back the original bytes, and a JSON response
has nowhere to put bytes that are not text, so the page shows what it could
decode and says so.

Empty body is the one place it differs on purpose: **400, not the CLI's exit 2**,
because there is no argv here to be called wrong with.

**A missing page file says which file is missing.** `margin web` out of a wheel
that failed to ship `margin/web/index.html` returns a 500 naming the path.
Letting the `open()` raise killed the connection with a traceback in the terminal
the user is watching for the URL, and the browser showed "connection reset" —
naming neither the file nor the cause. `src/package_test.py` exists to stop such
a wheel existing; if one does, the person running it gets told what is wrong.

Two numbers in `margin/web.py` are **bounds rather than measurements, and say so
in their own comments**: the default port 8731 (unassigned in the IANA registry,
stable enough to bookmark, falls back to an ephemeral port when taken — a second
`margin web` is a thing people do, not an error) and a 64 MiB body cap (~300x the
largest sample payload; the point is only that an unbounded read of
`Content-Length` is an out-of-memory a browser tab can trigger).

**It also prints the one thing the CLI never did.** When the lossy first stage
fires, the page names the keys it took — `4 link keys dropped and not
recoverable: html_url, …` — and shows nothing when it took nothing. The CLI now
prints the same fact on stderr. Neither did before, which was defensible while
everyone who reached this tool had read `docs/shapes.md` to get here; it stops
being defensible with a Copy button on screen, and CLAUDE.md's first rule is
reversibility and never delete-and-hope.

## Deliberately not built

- **`--decompress`.** One flag away and the tool's own inverse, but scope says
  one job; `python src/decompress.py f.txt` covers the rare occasion. The four
  subcommands above are not a reversal of this — see the section that answers it.
- **Multiple files, `-o`.** `>` and `|` already do both.
- **A TTY check on the summary line.** Printing stats only when stderr is a
  terminal is tempting and is the same shape as the `jq` bug in
  `run_eval.sh`: behaviour keyed to invisible state, silently doing nothing.
  Always on, `-q` off.
