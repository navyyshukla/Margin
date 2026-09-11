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

The questions are the payload's set — written out in prose for `github_issues` in
`data/eval_questions_github_issues.md`, and existing only as `src/checks_<payload>.py`
for the other seven — plus whatever
traps the new element deserves — an encoding that reads plausibly as something
else is the case worth constructing. Read #1's trap was decoding a `dints` array;
read #3's was resolving a `#dict` index.

**Record the result in a dated `docs/cold-reads/YYYY-MM-DD.md`, and add one row to
the table below.** The table is the single source of truth for the score —
`harness.md` Rule 10 and the root `README.md` cite it rather than keeping copies.

Read #5 adds a second way to run this, and `src/eval_model.py --emit` does the
setup: it writes one self-contained task per payload and arm, carrying the
document and the questions and nothing else — no ground truth, no repo path, not
even which arm it is. That last exclusion matters as much as the others.

## The running score

| Read | Answers | Confidence | What it found |
|---|---|---|---|
| [2026-09-08](cold-reads/2026-09-08.md) | 13/13 | 6/10 | drifted across a run of empty cells |
| [2026-09-08b](cold-reads/2026-09-08b.md) | 16/16 | 7/10 | miscounted 13 positional columns; `json` used for a plainly numeric column; `#keyed"_key"` had no delimiter; the dotted-path convention was never stated |
| [2026-09-09](cold-reads/2026-09-09.md) | **11/12** | 8/10 | could not verify an index 61 deep into an unmarked `#dict` array, and **miscounted 58 as 57**; the repeating header was never explained |
| [2026-09-10](cold-reads/2026-09-10.md) | 12/12 | 8/10 | a `#dict` cell reads as a value, not a reference (`1` label vs key `1`) — the store's own handles were unambiguous |
| [2026-09-11](cold-reads/2026-09-11.md) | **71/72** compressed, 69/72 stored, **72/72 raw** | not asked | `#keyed`'s synthetic `_key` column is reported as if it were a field of the record |

## Read #5 is a different instrument, and the table above flattens that

Reads #1–#4 are one reader thinking aloud, scoring its own confidence and saying
what was ambiguous. Read #5 is `src/eval_model.py`: 17 readers, one per payload
**and arm**, graded automatically against ground truth computed from the raw
payload.

It gains the thing no earlier read had — **a control arm.** The same questions
went to the uncompressed payload, so "the reader got it wrong" and "compression
cost the answer" are finally separable; that is how read #5's two stored misses
were shown to be arithmetic rather than data loss.

It loses the confidence score and the "say what was ambiguous" prompt, which are
what actually found the defects in #1–#4. **It does not replace them.** A new
header line or cell encoding still owes a read of the #1–#4 kind; read #5's kind
answers "did an answer move", which is a different question.

## What five reads have established

**Every read has found a real defect no automated gate could have seen**, and
confidence rose every time while the same weakness kept surfacing somewhere new —
which is the argument for running this on every format change rather than
trusting a good score.

**The weakness is counting.** All five reads have leaned on it. Reads #1 and #2
caught themselves by recounting; read #3 could not verify an index 61 entries
into an unmarked array and, on the very next question, miscounted 58 as 57.
"Caught by recounting" is luck about how careful the reader was, not a property
of the format.

Read #5 is the first one able to prove that diagnosis rather than assert it. Its
two stored-arm misses were both miscounts — and because it had a control arm
reading the *same* column in a different document, they could be shown to be the
reader rather than the format. Four reads suspected counting; the fifth
demonstrated it.

So the format has now paid twice to remove counting, deliberately:

| Change | Cost | Bought by |
|---|---|---|
| header repeats every 40 rows (`HEADER_REPEAT_EVERY`) | +0.3% | read #2 |
| `#dict` written as an object keyed by index, not a bare list | +0.2% | read #3 |
| `[Nt]` on every handle, so a reader can price a fetch before making it | +0.5% | priced *before* read #4, which then proved it |

Both times the alternative was to hope the reader counts carefully, and read #3
is what that hope looks like when it fails. **Correctness is not the same as
legibility, and only this test tells them apart.**

## What read #4 settled

It was run to answer one question: **does a reader who cannot fetch say so, or
does it answer from the columns around the handle?** It said so, twice, without
hedging — and it did more than refuse. Given only `[Nt]` on each handle it
answered which absent body was longest and what all of them would cost, correctly
and without retrieving anything, then said it would fetch *selectively*. That is
the behaviour the store depends on, observed rather than hoped for.

It also reconstructed the call — `fetch(document="22c8…", ids=[…])` — from the
legend alone, having never been told the tool exists.

## The next read

Owed when the next format line or cell encoding is added — and one is already
queued: **`_key`**, which read #5 showed is reported as a field of the record.
The candidate fix is one clause in the legend, and per the `harness` skill a
legibility change owes a read plus a re-measure.

Three things to watch, the first two recorded in read #4 and deliberately not
fixed, the third from read #5:

- **A `#dict` cell reads as a value.** `1` in a `labels:dict` column means "key 1
  on the `#dict` line" and sits beside columns where a small integer is genuinely
  a count. No answer in five reads has been wrong because of it; the read that
  gets one wrong is the one that buys the fix.
- **Counting.** Every read has leaned on it. Read #4 leaned hardest and held;
  read #5 finally caught it failing, twice, with a control arm to prove it was
  the reader and not the document.
- **`_key` as data.** Read #5's finding, above. Unfixed.
