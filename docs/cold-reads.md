# The cold read — the procedure, and the running score

The one check no code performs: **can a model that has never seen this project
read a `#margin/v1` document correctly?**

Every automated gate runs its checks on *decompressed* data, so none of them ever
reads the document at all. And Claude's own reading does not count — it designed
the format and knows what every marker means without being told. `harness.md`
Rule 10 is the rule; this file is how to run it and what it has found.

## When it is owed

Whenever a **new header line or a new cell encoding** is added. That is precisely
what a reader cannot infer from the document and what no automated gate can see.
Not owed for a threshold change, a refactor, or a new payload in an existing
shape.

## Method

State it once here; a read's own file records only what it found.

A fresh reader — a model with **no access to this repo** — is given only the
rendered `.txt` document(s) and told:

- read nothing else: no source, no docs, no original JSON
- write no code to parse them — read the way a prompt is read
- answer **"not answerable from this document"** rather than guess
- say bluntly what was ambiguous, and give a confidence score out of 10

The questions are the payload's set from `data/eval_questions.md`, plus whatever
traps the new element deserves — an encoding that reads plausibly as something
else is the case worth constructing. Read #1's trap was decoding a `dints` array;
read #3's was resolving a `#dict` index.

**Record the result in a dated `docs/cold-read-YYYY-MM-DD.md`, and add one row to
the table below.** The table is the single source of truth for the score —
`harness.md` Rule 10 and the root `README.md` cite it rather than keeping copies.

## The running score

| Read | Answers | Confidence | What it found |
|---|---|---|---|
| [2026-09-08](cold-read-2026-09-08.md) | 13/13 | 6/10 | drifted across a run of empty cells |
| [2026-09-08b](cold-read-2026-09-08b.md) | 16/16 | 7/10 | miscounted 13 positional columns; `json` used for a plainly numeric column; `#keyed"_key"` had no delimiter; the dotted-path convention was never stated |
| [2026-09-09](cold-read-2026-09-09.md) | **11/12** | 8/10 | could not verify an index 61 deep into an unmarked `#dict` array, and **miscounted 58 as 57**; the repeating header was never explained |

## What three reads have established

**Every read has found a real defect no automated gate could have seen**, and
confidence has risen every time while the same weakness kept surfacing somewhere
new — which is the argument for running this on every format change rather than
trusting a good score.

**The weakness is counting.** All three reads hit it. Reads #1 and #2 caught
themselves by recounting; read #3 could not verify an index 61 entries into an
unmarked array and, on the very next question, miscounted 58 as 57. "Caught by
recounting" is luck about how careful the reader was, not a property of the
format.

So the format has now paid twice to remove counting, deliberately:

| Change | Cost | Bought by |
|---|---|---|
| header repeats every 40 rows (`HEADER_REPEAT_EVERY`) | +0.3% | read #2 |
| `#dict` written as an object keyed by index, not a bare list | +0.2% | read #3 |

Both times the alternative was to hope the reader counts carefully, and read #3
is what that hope looks like when it fails. **Correctness is not the same as
legibility, and only this test tells them apart.**

## The next read

Cold read #4 is owed by the store: `#store` is a new header line and a handle is
a new cell encoding. It also introduces a question none of the first three asked,
and the one to design the read around — **a reader who cannot fetch must say so
rather than infer.** Give it a handle-bearing document and no store access, and
ask questions whose answers live behind a handle. "I need `@a1b2` to answer this"
is the pass; anything reconstructed from neighbouring columns is the failure, and
it is the failure mode that would make the whole store stage worse than useless.
