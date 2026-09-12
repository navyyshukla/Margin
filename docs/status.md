# Where things stand

> **A note on the PR numbers below.** They are this file's own sequence and it
> is **not** GitHub's. It went one ahead at the store, because what this file
> calls PR #5 (the format and the store) and PR #6 (MCP server and CLI wiring)
> shipped as a single pull request — [GitHub #5][5], whose title carries the
> 73.7% both halves were needed for. Everything after inherits the offset:
>
> | here | on GitHub |
> |---|---|
> | #1, #2, #3, #4 | [#1][1], [#2][2], [#3][3], [#4][4] — same |
> | #5 *and* #6 | [#5][5] — one PR, both stages |
> | #7 the model-in-the-loop eval | [#6][6] |
> | #8 `_key` said plainly | [#7][7] |
> | the miscount isolation | [#8][8] |
> | #10 the judged half | [#9][9] |
> | #11 the store becomes recoverable | [#10][10] |
>
> The headings are left as they are: the merge commits on `main` already cite
> **GitHub's** numbers, so that path is correct for anyone reading the history,
> and renumbering here would break every cross-reference in `docs/` to buy
> nothing the table above does not.

[1]: https://github.com/navyyshukla/Margin/pull/1
[2]: https://github.com/navyyshukla/Margin/pull/2
[3]: https://github.com/navyyshukla/Margin/pull/3
[4]: https://github.com/navyyshukla/Margin/pull/4
[5]: https://github.com/navyyshukla/Margin/pull/5
[6]: https://github.com/navyyshukla/Margin/pull/6
[7]: https://github.com/navyyshukla/Margin/pull/7
[8]: https://github.com/navyyshukla/Margin/pull/8
[9]: https://github.com/navyyshukla/Margin/pull/9
[10]: https://github.com/navyyshukla/Margin/pull/10

Moved out of `CLAUDE.md` on 2026-09-10. Status prose ages badly and CLAUDE.md is
re-read into every session, so a stale claim there is a stale claim the model
acts on — which has already happened once: the file asserted structural
compression was nearly exhausted, `#dict` disproved it the next day, and the
claim had never been measured. **Date every claim here, and re-measure rather
than remember (`harness.md` Rule 5).**

## The compressor is finished (2026-09-09)

Three PRs, all merged: #1 the compressor, #2 the `margin` CLI, #3 `#dict`. Eight
payloads from eight APIs, **121,569 → 71,034 tokens (41.6%)**, worst case 0.0% —
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
**60% of what remains (42,334 of 71,034 tokens) is bulk content** — free text plus
HackerNews's 6,662 comment IDs — and it is 89% of `jsonplaceholder`, 88% of
`github_issues`, 75% of `hn_stories`.

A first attempt at that measurement counted only prose over 200 characters, put
the figure at "one payload out of eight", and nearly settled the question the
wrong way. It missed `jsonplaceholder`'s short bodies and excluded `children` for
not being prose. The correction is recorded in `docs/shapes.md`, because the
wrong version was about to become the reason not to build this.

**Planned sequence.** Measure first → format + store + completeness gate → MCP
server + CLI wiring → cold read #4 and a model-in-the-loop eval.

- [x] **Measured** (2026-09-10). `src/measure_store.py` prices every cell against
      a handle and renders the resulting document. Projected 73.5% for the
      encoding that shipped; the shipped CLI measures **73.7%** across the set
      (re-measured 2026-09-11), concentrated in three payloads. This line said
      74.4% until then — the projection's *bare-handle* row, not its
      `\@0001[276t]` one. Numbers in `docs/shapes.md`, thresholds and the
      handle-encoding comparison in `docs/thresholds.md`.
- [x] **PR #5 — the format and the store.** `\@0001` handles, a per-document id
      with the `id -> hash` index on disk, `src/store.py`. The CLI is untouched:
      `store=None` is the default, so every existing gate still exercises the
      storeless path. property_test 48 fixed cases with 636 of 2,000 random
      trials building a `#store`; mutation_test 11 → 17, all caught.
- [x] **PR #6 — MCP server and CLI wiring** (2026-09-10). `--store` off by
      default; `fetch(document, ids, query)` over stdio, batched, searchable and
      capped. The savings line now reads "in the prompt" and states what is being
      held, because content moved to disk is not content removed. Hash widened to
      96 bits and the index made atomic, both from the Headroom review. Full
      picture: `docs/store.md`.
- [x] **Cold read #4** (2026-09-10). 12/12. Given a handle-bearing document and
      no way to fetch, the reader said "not answerable" rather than inferring —
      the failure that would have made this stage worse than useless. The `[Nt]`
      lure paid for itself in the same read. `docs/cold-reads/2026-09-10.md`.
- [x] **PR #7 — the model-in-the-loop eval** (2026-09-11). `src/eval_model.py`,
      a script rather than an eighth gate: non-deterministic, networked and
      billable are three separate reasons a gate gets bypassed (Rule 12).
      Three arms — raw / compressed / stored — because building it only for the
      store would have left the 41.6% compressor permanently unmeasured.

      **First result, 72 graded questions across eight payloads:**

      | arm | correct |
      |---|---|
      | raw (reference) | **72/72** |
      | compressed | **71/72** |
      | stored | **69/72** |

      Read by fresh Claude subagents, one per payload and arm, each given the
      document and nothing else — the `docs/cold-reads.md` method, automated and
      scored. The Gemini path exists and works but the free tier is ~20 requests
      per **day** per model against ~194 for a sweep, and this project does not
      buy quota.

      **The one compressed miss is a real format defect.** Asked for the complete
      `ethereum` record, the reader returned all 12 fields correctly plus a 13th:
      `_key`, which is `#keyed`'s synthetic column. Nothing is lost — `decompress`
      strips it and the round-trip gate has always passed — but a *reader* reports
      Margin's scaffolding as data, and the legend line did not stop it. This is
      the class of defect no gate here can see, found by a machine for the first
      time rather than by a human cold reader. **Fixed 2026-09-12, below.**

      **The two stored misses are reader noise, not data loss** — verified:
      `comments` decompresses identically in both arms (11 zeros, 31 total), no
      `comments` cell is a handle, and the compressed arm answered both
      correctly. One reader, one sample, arithmetic. **Reproduced 2026-09-11's
      finding exactly on 2026-09-12** with a different reader, which makes it two
      samples and still unexplained.

## PR #8 — `_key` said plainly, and retrieval measured more than once (2026-09-12)

The two things cold read #5 left open, shipped together because the first owes a
re-measure and a cold read that the second's questions ride along on.

- [x] **The `#keyed` legend says what `_key` is *not*.** One clause, +12 tokens,
      `coingecko_prices` 30.6% → **29.6%** and nothing set-wide (71,022 → 71,034,
      41.6% either way). Derivation in `docs/thresholds.md`.

      Nothing guarded that sentence before: `decompress` strips the column either
      way, so round-trip cannot see a legend (Rule 3). `property_test.py` now
      asserts the clause names the **actual** column — the collision fixture keys
      on `_key3` — and reports how many fixed cases were keyed, because the random
      sweep never builds a record map (Rule 2). `mutation_test.py` 24 → 25, the
      new one reverting to the exact pre-fix wording rather than deleting the
      clause.

- [x] **Three graded questions that cannot be answered without fetching.**
      `Q14`/`Q15` on github (bodies) and `H14` on hn (a stored `children` array).
      Graded questions **72 → 75**; the eight `checks_*.py` modules now hold 80
      checks — counted at import time, not by reading the list literals, because
      `checks_pokeapi_ditto.py` appends one after its own. Counting the literals
      is how this line first said 79 and how "76 checks" had been wrong at 77
      since before this PR (review, 2026-09-12).

- [x] **Cold read #6** (`docs/cold-reads/2026-09-12.md`), the #1–#4 kind: 10/10,
      confidence **9/10** — the highest of seven reads. C8 correct, `_key`
      excluded, and the reader named the new clause as what decided it while
      still calling the question a judgment call. Everything it flagged was a
      defect in the *questions*, not the document.

- [x] **Cold read #7, the sweep** (`docs/cold-reads/2026-09-12-sweep.md`), 75
      graded questions:

      | arm | correct | was |
      |---|---:|---:|
      | raw (reference) | **75/75** | 72/72 |
      | compressed | **75/75** | 71/72 |
      | stored | **73/75** | 69/72 |

      **The stored arm fetched 4 times, on all three payloads that carry a
      store**, and every retrieved answer was right — including `H14`, where the
      reader fetched a `dints` cell and applied the legend's "first is absolute"
      clause to a value that was not in the document at all.

      **The run's own verdict is FAIL**, and it is reported rather than softened:
      `GRADED_ALLOWED_GAP = 0` and the stored gap is 2. The two misses are `Q5`
      and `Q6` — the same two as 2026-09-11, a different reader, a column that is
      byte-identical across arms and contains no handle, which the same reader
      counted wrong immediately after fetching two bodies correctly. Counting,
      now demonstrated twice, with no candidate fix: a count is not a marker a
      legend clause can explain.

- [ ] **Still owed, and named rather than quietly dropped:**
      - ~~The **16 comprehension questions are unmeasured.**~~ **Measured
        2026-09-12** — see below.
      - **Why a reader that has fetched sometimes misreads a row.** Narrowed
        2026-09-12 by `src/measure_miscount.py`, 25 readers over five arms
        (`docs/cold-reads/2026-09-12-miscount.md`): the document is **not** the
        cause (20 readers with no fetch tool, 0 misses, on the same bytes), the
        size of the task is not either, and `[Nt]` — the leading suspect — was
        cleared. It is also **not counting**: the failing reader misread one row
        and totalled its own list correctly. What is left is one miss in five in
        the only arm that could fetch, which is a suspect and not a rate. The
        arm that would separate "the turns" from "the retrieved text in the
        context" is named there and not built.
## PR #10 — the judged half, measured without buying a judge (2026-09-12)

`JUDGED not measured` had printed on every run since `eval_model.py` existed,
because judging needed `JUDGE_MODEL` and this project does not buy quota. The
expensive half turned out to be already done: `--emit` has been writing the 16
comprehension questions into every task all along, readers have been answering
them, and `--grade` stored the prose and scored nothing. No run file had ever
contained `same_as_raw`.

- [x] **`--emit-judge` / `--judge-in`**, the same road `--emit`/`--grade` took.
      The run-file format did not change: `summarise()` only ever needed a
      boolean per record.

      | arm | GRADED | JUDGED |
      |---|---:|---:|
      | raw (reference) | 75/75 | **16/16** |
      | compressed | 75/75 | **16/16** |
      | stored | 73/75 | **16/16** |

- [x] **The first run was worthless and the controls are why we know.** It came
      back "same" 18 times out of 18 — indistinguishable from a judge that cannot
      say "different" (Rule 14: *a detector nobody has shown a positive to is not
      a detector*). `--emit-judge` now interleaves controls whose answer is known
      in advance, `--judge-in` checks them before writing anything, and a failed
      control produces **no judged number at all**. The judge scored **16/16 on
      the controls, 8 of them pairs it had to call different**.

- [x] **A blinding leak, found in the place nobody looks.** The prompt was
      rewritten to stop naming the arms — and the task *filenames* still said
      `__compressed` / `__stored`. Tasks are `judge_001.md` now.

- [x] **Three checks.** `self_test` 6 cases → 9 (`_fake_run` hardcoded
      `"judged": []`, so every judged failure mode was unreachable from the one
      thing guarding the verdict); `inconclusive_reasons` gained a judged
      denominator condition; the control gate itself, shown a positive.

**The weakness, on the record:** judge and answerer are the same model family.
The controls show this judge can tell answers to different questions apart; they
do not show it free of a shared prior with the model whose work it marks. Full
statement in `docs/cold-reads/2026-09-12-judge.md`.

**It does not offset the graded failure.** The stored arm is still 73/75 and the
run still says `THRESHOLDS NOT MET`.

## PR #11 — the store becomes recoverable, and that is the last of it (2026-09-12)

The two gaps `docs/store.md` had named since the store shipped, and the reason
they beat the scope rule that kept `--decompress` out of the CLI: a
`--decompress` flag is a convenience for something `python src/decompress.py`
already does, while **a store with no `fsck` and no bundle unit is unrecoverable
by any other means.**

- [x] **Four subcommands.** `margin fsck` (every index against the objects it
      names), `margin gc [--delete]` (objects nothing references, **dry run by
      default** — the only irreversible thing here), `margin export <doc-id>
      <file>` and `margin import <file>`. `margin f.json` is byte-identical to
      what it was; the dispatch happens before anything treats a word as a path,
      and `docs/cli.md` states the cost — a file literally named `export` is now
      ambiguous.

- [x] **The one surface the store lacked: enumeration and deletion**, on
      `MemoryStore` and `FileStore` both (Rule 15). Everything else reuses what
      was already there — `missing_handles`, `orphaned_ids`, `commit`, and
      `get`'s existing re-hash, which is why `fsck` needed no new integrity code.

- [x] **Verified end to end**, not just in unit checks: a document compressed
      with `--store`, exported, imported into a second empty store, and
      decompressed there — 30 records, bodies intact. Then the failure
      `docs/store.md` predicted: delete the index, `fsck` reports 32 leaked
      objects, `gc` lists them, `gc --delete` sweeps them, `fsck` says clean.

- [x] **Mutation coverage 25 → 31**, and **two of the six survived the first
      time** — the useful part. "gc marks from one index instead of every index
      in `docs/`" survived because the check written for it held a *single*
      document, so marking from one index and from all of them were the same
      thing; the cross-document property that is the whole point of GC was
      untested by its own test. And "import writes the objects and never the
      index" made the check *explode* rather than fail, and a crashed gate
      reports no failure line — the exact trap `detects_a_broken_store` records
      one screen above where it happened.

- [x] **Declined on the record rather than left pending:** Headroom's TTL and
      retrieval counts (a policy for a store under memory pressure; `gc` now
      bounds growth for the one this is) and Parquet-style per-column statistics
      (promising and entirely unmeasured, which is the project's standing bar —
      the same call `graphql_countries.emoji` got).

## Done (2026-09-12)

**The project is finished.** Not "no ideas left" — the scope CLAUDE.md set is
met and measured:

| | |
|---|---|
| compression | **41.6%** across eight APIs, worst case 0.0% |
| with the store | **73.7%**, and the store is now recoverable |
| answers, graded | raw 75/75, compressed **75/75**, stored 73/75 |
| answers, judged | **16/16** on every arm, behind a judge gated by controls |
| the harness | 8 gates, 80 checks, 31 mutations, all caught |
| cold reads | 8, every one of the first five finding a defect no gate could see |

**Two things are open and neither is work anyone is waiting on:**

1. **The stored arm fails its own bar, 73/75, and it has been left red across
   three PRs.** The cause is narrowed, not fixed: not the document, not `[Nt]`,
   not task size, and not "counting" — a reader that has fetched sometimes
   misreads a row (`docs/cold-reads/2026-09-12-miscount.md`). 1 miss in 5 is a
   suspect, not a rate. Widening `GRADED_ALLOWED_GAP` to make the run green is
   the one thing that would make all of this worthless, and it has not been done.
2. **The judge shares a model family with the answerer.** The controls show it
   can say "different"; they do not show it free of a shared prior. An
   independent judge would be the stronger instrument and would cost money.

Everything else was declined in writing, with a reason, at the time:
`graphql_countries.emoji`, `--decompress`, multiple files and `-o`, a TTY check
on the summary line, TTL and retrieval counts, per-column statistics.
