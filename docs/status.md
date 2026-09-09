# Where things stand

Moved out of `CLAUDE.md` on 2026-09-10. Status prose ages badly and CLAUDE.md is
re-read into every session, so a stale claim there is a stale claim the model
acts on — which has already happened once: the file asserted structural
compression was nearly exhausted, `#dict` disproved it the next day, and the
claim had never been measured. **Date every claim here, and re-measure rather
than remember (`harness.md` Rule 5).**

## The compressor is finished (2026-09-09)

Three PRs, all merged: #1 the compressor, #2 the `margin` CLI, #3 `#dict`. Eight
payloads from eight APIs, **121,569 → 71,022 tokens (41.6%)**, worst case 0.0% —
nothing ever comes out larger than it went in. Per-payload numbers and the shapes
behind them: `docs/shapes.md`.

Verified again 2026-09-10: all four gates exit 0, including all eight payloads
through `eval_harness.py` and all 11 mutations caught by the check named for each.

**Do not open another compression rule without a measured reason.** The remaining
headroom is one payload-specific trick worth 1,500 tokens; the other 60% belongs
to the store.

`#dict` (2026-09-09) closed the gap `#const` left: a column drawn from a handful
of repeated values is stated once and indexed. Worth 4,044 tokens net — GitHub
53.1% → 55.3%, GraphQL countries 11.3% → **34.4%** — after paying 0.3% back for
legibility (cold read #3).

**It is a tool you can actually use** (2026-09-09): `curl ... | margin | pbcopy`.
Reads a path or stdin, document on stdout and everything else on stderr, and
"returned the input unchanged" means byte-for-byte. Exit codes and the four
decisions behind them: `docs/cli.md`; `src/cli_test.py` is the gate.

Building it bought two harness rules, which is the point of building it: four
green gates had never seen an argument, a stream or an exit code, and
`margin f.json | head -1` — the way you look at a document — printed a
BrokenPipeError traceback (Rule 11). A "skip when there's no `.venv`" in the new
test turned out to be the *only* branch pre-commit could ever take (Rule 12).

## Structural compression is finished (2026-09-09)

The only candidate left in the sample set is `graphql_countries.emoji` (1,500
tokens, derivable from the `code` column since a flag emoji is its ISO code in
regional-indicator characters) — **declined**, because one rule for one API is
the over-reach that "one job, done well" forbids. Everything else needs the store.

## The next stage: the store (opened 2026-09-10)

82% of `github_issues.json`'s output is `body` prose, which no rule compresses
losslessly. Going further means **not putting bulk content in the prompt at
all** — a reversible store the model queries — which turns Margin from a text
filter into a **tool the model calls**, and deliberately reverses "no proxy
server, not yet". Worth ~92% on GitHub.

`docs/shapes.md` said to decide it against all eight payloads rather than the two
that existed when it was first raised. Measured properly, in document terms:
**60% of what remains (42,334 of 71,022 tokens) is bulk content** — free text plus
HackerNews's 6,662 comment IDs — and it is 89% of `jsonplaceholder`, 88% of
`github_issues`, 75% of `hn_stories`.

A first attempt at that measurement counted only prose over 200 characters, put
the figure at "one payload out of eight", and nearly settled the question the
wrong way. It missed `jsonplaceholder`'s short bodies and excluded `children` for
not being prose. The correction is recorded in `docs/shapes.md`, because the
wrong version was about to become the reason not to build this.

**Planned sequence.** Measure first (re-confirm the 42,334 figure holds after
`#dict`, and project per-payload savings) → format + store + completeness gate →
MCP server + CLI wiring → cold read #4 and a model-in-the-loop eval. The harness
work this needs is in the `harness` skill's checklist; the new ground is that
`eval_harness.py` checks answers against *decompressed* data, so it never
exercises the decision to **call the tool**, which is the entire value of the
stage.
