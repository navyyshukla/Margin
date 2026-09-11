# The store, and how a model reaches it

Structural compression finished at 41.6%. **60% of what remained was bulk
content** — free text plus HackerNews's comment IDs — that no lossless rule
reduces (`docs/shapes.md`). The only way past it is to stop putting that content
in the prompt at all: replace the cell with a handle, keep the content on disk,
and let the model fetch what a question actually needs.

That turns Margin from a text filter into **a tool the model calls**, and it
deliberately reverses CLAUDE.md's "no proxy server, no hosted API, not yet".

| | now | with the store |
|---|---:|---:|
| `github_issues.json` | 55.3% | **93.4%** |
| `hn_stories.json` | 43.0% | **88.1%** |
| `jsonplaceholder_posts.json` | 26.2% | **71.5%** |
| the other five payloads | | **unchanged, byte for byte** |
| whole sample set | 41.6% | **73.7%** |

Every figure `margin --store` output, re-measured 2026-09-11. The middle two rows
read `~89%` and `~75%` until then — approximations carried over from the
projection, and the second was a point and a half optimistic. An `~` is not a
measurement; `docs/shapes.md` records what the rounding hid.

Three payloads of eight, and nothing at all for five. That order matters: the set
total is driven entirely by the three, and `docs/shapes.md` records what
overstating this cost the first time it was measured.

## The format

```
#margin/v1
#legend \N = null; \@nnnn[Nt] = this value is NOT in this document; N is what it
        costs in tokens. Resolve with the margin `fetch` tool: fetch(document=…)
#store"22c8424b6010d312e67b69d6"
[30]{number:int,title:str,body:str}
37537,Preserve JSX pragmas,\@0001[276t]
```

- **`\@0001[276t]`** — a stored cell. `0001` is its id in this document; `276t`
  is what fetching it costs, so a reader can decide before spending.
- **`#store"<id>"`** — names the index these ids belong to.

The `\@` marker lives inside the existing backslash namespace (`\N`, `\E`, `\A`),
so **no new escaping rule was needed**: `encode_cell` already doubles a leading
backslash, which means a literal cell value of `\@0001` writes as `\\@0001` and
the two can never be confused. A bare `@` would have needed a rule of its own,
and every cold read so far has found its defects in what a reader must infer.

## Two levels, and why

The document carries a **short per-document id**; the store keeps an `id → hash`
index on disk and names objects by a 96-bit content hash.

Putting the hash straight in the document is the obvious design and was measured
and rejected: it costs **1.8 points, ~2,200 tokens** across the sample set
(`docs/thresholds.md`). The index buys the short id back without giving up
content addressing, because the index is read from disk and never enters a
prompt.

Content addressing survives the indirection and earns its keep two ways:
**dedup** — the same issue body fetched twice is stored once — and **integrity**,
since `get` re-hashes what it reads rather than trusting the filename.

Dedup is a *cross-document* property and only that. Inside one document, a
repeated bulk value is claimed by `#const` and a few repeated ones by `#dict`,
both of which run before the store sees a cell. That is the existing rules
working, and it is why the dedup gate compares two documents.

```
~/.margin/store/objects/<24-hex>      one stored value, verbatim UTF-8 bytes
~/.margin/store/docs/<doc_id>.json    {"0001": "<hash>", …}
```

Under `$HOME`, not the working directory: a document compressed in one directory
must still resolve elsewhere, and a relative root would make "can I read this?"
depend on where you happen to be standing. `$MARGIN_STORE` overrides.

## `--store` is off by default

`curl … | margin | pbcopy` is the everyday invocation and what comes out of it
gets pasted into a chat. A document with handles is readable only by a client
wired to this machine's MCP server — paste it anywhere else and the model does
not merely lack the store, **it has no fetch tool at all**, so the handles are
dead and the values silently missing at exactly the moment someone is relying on
them.

Storing is valid only when a resolver is in the loop. So the default path is
unchanged, byte for byte, and the summary line stops saying "saved" when a store
is in use:

```
margin: 50,031 → 3,298 tokens (93.4% in the prompt)
margin: 32 value(s) held in the store — fetchable, not discarded
```

"93.4% saved" would be a lie of omission. The old number meant the reader reads
that much less; this one means that much went somewhere the reader must go and
get.

## The `fetch` tool

`src/mcp_server.py`, over stdio:

```
claude mcp add margin -- <repo>/.venv/bin/python <repo>/src/mcp_server.py
```

`fetch(document, ids, query=None)`, and each part of that signature is a
deliberate answer to something retrieval tools get wrong:

- **`ids` is a list.** Models resolve handles one per turn if allowed, and every
  round trip re-serialises the conversation.
- **`query` searches within a value** and returns only matching spans. Most
  fetches want one paragraph of a 4,000-token body. Borrowed from Headroom's
  `headroom_retrieve(hash, query?)`. Spans are merged, and if they do not come to
  less than the value itself the whole value is returned — a search that returns
  more than the thing searched is worse than no search.
- **Responses are capped at 8,000 tokens and say when they truncate.** A model
  that does not know it got a partial answer answers as though it got all of it.

Missing content **raises or reports `MISSING`** — never an empty string, and
`decompress` refuses outright on a document whose store is absent. A store is the
first thing here capable of losing half a document while looking fine
(`docs/harness.md` Rule 14).

## What a cold reader made of it

`docs/cold-reads/2026-09-10.md`, 12/12. Given a handle-bearing document and **no
way to fetch**, the reader said "not answerable — the body is elided" rather than
inferring from neighbouring columns, which was the failure that would have made
this stage worse than useless. It answered which absent body was longest and what
all of them would cost — correctly, from `[Nt]` alone — and reconstructed
`fetch(document="22c8…", ids=[…])` from the legend, never having been told the
tool exists.

## Not built, and said out loud

- **`margin export` / `import`.** A document is meaningless without its index and
  objects, and there is no bundle unit. A real gap.
- **GC and `fsck`.** With the index rather than hashes in the document, garbage
  collection must mark from `docs/`, so a lost index leaks objects forever.
- **TTL and retrieval counts.** Headroom evicts on a 30-minute TTL and feeds
  retrieval counts back into how aggressively content is compressed next time —
  content fetched every time should stop being offloaded. A fixed
  `MIN_STORE_SAVING` cannot express that.
- **Per-column statistics** (Parquet-style min/max/null-count), so a reader could
  skip fetches entirely for many questions. Promising and entirely unmeasured,
  which is exactly why it is not built.

**The measurement that was owed, and what it came back with (2026-09-12).**
Every figure above is about *documents*, and `src/eval_model.py` asks whether
answers survive too. Across 75 graded questions and eight payloads, read by fresh
readers given the document and nothing else: **raw 75/75, compressed 75/75,
stored 73/75** (`docs/cold-reads/2026-09-12-sweep.md`).

**Retrieval is no longer one fetch deep.** The 2026-09-11 run queried the store
exactly once, on `jsonplaceholder`, because no graded question read a stored
value on the other two payloads. Three questions were added for that —
`Q14`/`Q15` on github bodies, `H14` on an hn `children` array — and the stored
arm now **fetches 4 times across all three payloads that carry a store, and gets
every retrieved answer right.** `H14` is the sharpest of them: the reader fetched
a delta-encoded cell that is not in the document at all and read its first value
correctly off the legend's "first is absolute" clause.

**The stored arm still fails its own bar, and the reason is not the store.** Its
two misses are `Q5` and `Q6` on github — how many issues have zero comments, and
their total. `comments` is not stored: no cell in that column is a handle, the
column decompresses identically in both arms, and the compressed and raw readers
both answered correctly.

**Isolated 2026-09-12** (`src/measure_miscount.py`,
`docs/cold-reads/2026-09-12-miscount.md`): 25 readers across five arms. The
twenty given no fetch tool missed **nothing** — including ten given the exact
stored bytes that had failed twice — and the run's single miss came from the one
arm that could fetch, where the reader misread one row and then totalled its own
list correctly.

So the document is not what causes it, the size of the task is not either, and it
is a misread row rather than the "counting" seven cold reads had assumed. What
remains is a suspicion about having fetched — whether the turns or the retrieved
text left in the reader's context — at 1 miss in 5, which is **not a rate**.
`GRADED_ALLOWED_GAP = 0` still reports FAIL, and is not being widened to fit a
result it exists to catch.

The `[Nt]` size on each handle was the leading suspect and was **cleared**: a
bare-handle arm read the same document just as well.
