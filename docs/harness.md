# The harness, and the rule each part enforces

Four gates and ten rules. Each rule exists because something got past the gates
before it, and each is written down with the failure that bought it — a rule
whose reason is forgotten is a rule someone deletes.

| Gate | Asks | Needs a sample payload? |
|---|---|---|
| `src/property_test.py` | Does the format survive shapes nobody wrote down? | no — generates its own |
| `src/eval_harness.py` | Do the answers survive on the payloads I have? | yes |
| `.claude/hooks/run_eval.sh` | Did Claude's last edit break either? | no |
| `.githooks/pre-commit` | Is this commit allowed to exist? | no |

Run `./.githooks/install.sh` once per clone, and **again after editing any hook** —
hooks are copied into `.git/hooks`, not symlinked. (`core.hooksPath` is
deliberately not used: hooks are working-tree files, so on a branch predating
them the file is absent and nothing runs, which defeats the hook whose whole job
is guarding `main`.)

---

## Rule 1 — A test of a format must assert the format was used

**`compress_json` verifies its own round-trip and falls back to plain JSON when
it fails.** That is the right behaviour and it is also a trap: a completely
broken table encoder still satisfies
`decompress(compress(x)) == strip_boilerplate(x)`, because the fallback
round-trips trivially.

Both tests were written without this and both were worthless until it was added:

- The **eval harness** was checked by deliberately collapsing `MISSING` and
  `None` in `encode_cell`. Round-trip passed. All 11 answer checks passed — the
  answers are all present in the fallback JSON. Only the NOTES section failed.
- The **property test** was checked by reintroducing the `["\N"]` bug. 2,000
  trials reported zero failures until the notes check was added.

So: `eval_harness.py` fails on a `ROUND-TRIP MISMATCH` note, and
`property_test.py` fails on `ROUND-TRIP MISMATCH` or `table render/parse
failed`. Notes about a payload not being *worth* a table (too few rows, saving
below the gate) are the gate working, not a failure.

**Applies to any future stage.** If a stage can degrade safely, its test must
check that it did not degrade.

## Rule 2 — A generator that never triggers the code path proves nothing

The random sweep ran 2,000 trials and built **0 tables**. Two causes, both
worth remembering:

- **Rows too few.** 2-3 row payloads fail `MIN_TABLE_SAVING` and come back as
  JSON. Payloads are now 6-20 rows.
- **Columns too heterogeneous.** Rolling an independent type per *cell* makes
  every column `json`, and a table of `json` columns always loses. Real API
  responses are homogeneous down a column, so the generator now picks a value
  family per column and varies values within it.

`property_test.py` prints how many trials built a table and warns loudly at
zero. **Any sweep that can silently test nothing must report its own coverage.**

## Rule 3 — Round-trip equality cannot see a lie in the header

`decompress(compress(x)) == x` says the *data* survived. It says nothing about
what the document *claims*, and the document is what the model reads.

A column holding nothing but empty arrays satisfies "every element is an int"
vacuously, so it was declared `dints` — delta-encoded integers. `hn_stories.json`
shipped `_highlightResult.matchedWords:dints?` for a column of Algolia
*strings*. Every cell was `\A` either way, so no round-trip could ever notice.

Enforced by `type_claims_are_backed` in `property_test.py`: no column may
declare an array encoding it has zero elements of evidence for.

**Anything the document asserts to the reader needs a check aimed at the
assertion, not at the data.**

## Rule 4 — Encoder and decoder must dispatch on the same thing

`encode_cell` dispatched on the *value's* Python type; `decode_cell` dispatches
on the *column's declared* type. For a mixed-type (`json`) column they
disagreed: the string `"a,b"` was written bare and crashed `json.loads` coming
back, and worse, the string `"true"` was written as `true` and read back as the
**boolean**, `"3.0"` as the float.

Data was never lost — the round-trip check caught it and fell back — but every
payload with a mixed column containing strings silently lost its table, and
mixed columns are common. Found by `property_test.py` on its first real run.

**Where two functions are inverses, they must key off the same source of truth.**
That is also why writer and reader live in one file: `src/render.py`.

## Rule 5 — Every number in a comment must be re-measured, not remembered

`legend_for`'s docstring claimed 58 tokens; the line actually costs 44. Caught
by review, not by a test. CLAUDE.md already requires thresholds to be measured;
this extends it to numbers quoted in prose.

Two specific traps found this way:

- **Quote figures against the raw file, not `json.dumps`.** `json.dumps` adds
  `", "` and `": "` that were never on disk. HN's "18.6% saving" was measured
  against `json.dumps` output; against the actual file it was **0.3%**.

  This rule first read: "the gate must keep using `json.dumps` — that is what it
  would emit instead — but anything said to a human uses the raw file." **That
  conclusion was wrong, and leaving it written down cost a real bug.** The
  padding was not a measurement artefact to compensate for in reports; it was
  in the output. Three of the six payloads added the next day came back *larger*
  than they went in — Open-Meteo by 19%, exchange rates by 25% — because the
  fallback emitted padded JSON. The fix was compact separators everywhere, which
  corrected the output and the denominator at once.

  The general lesson, which is why this stays: **when a measurement looks wrong,
  check whether the thing being measured is wrong before writing a caveat about
  how to read the number.** A caveat is how a bug gets documented instead of
  fixed.
- **A fixed seed, always.** A gate that fails one commit in twenty gets
  bypassed, and a bypassed gate is not a gate. Hunt for new bugs by passing a
  different seed explicitly.

## Rule 6 — A test case must declare what should happen, not just "it worked"

"It round-tripped" is not the same claim as "the table was correct". The
compressor is allowed to abandon a table and emit JSON, and that is the right
answer for a key containing a comma and the wrong answer for a column of sorted
integers — but both round-trip perfectly.

So `property_test.py` splits its regression cases:

- **`MUST_TABULATE`** — the encoder has to get these right, not dodge them. A
  case here that degrades has found a regression.
- **`MAY_DEGRADE`** — the format genuinely cannot express these (a key with a
  `,`, `.` or newline; a cell containing a line identical to the header). The
  requirement is that they degrade rather than crash, and that the data survives
  exactly. A case here that degrades has found nothing.

Without the split, every case sat in one list and the "must" cases quietly
passed by falling back.

**Corollary: make the case big enough to reach the code path.** A two-row
payload fails `MIN_TABLE_SAVING` and comes back as JSON, so a two-row regression
test for a table bug tests the fallback instead. `repeated()` builds eight rows
for exactly this reason.

## Rule 7 — Round-trip cannot see *which* records were chosen

A payload with more than one record array tabulates the largest. Tabulating the
wrong one round-trips perfectly — it just compresses far less and leaves the
interesting data sitting in `#wrap` as raw JSON. No equality check can notice.

`EXPECTED_PATHS` in `property_test.py` pins the answer for a bare array, a
wrapped one, two nesting depths, and the case where a small incidental list
comes before the real records in dict order.

Same shape as Rule 3: **when a choice is invisible to the round-trip, the choice
needs its own assertion.**

## Rule 8 — Unfinished is not the same as broken

A payload in `data/samples/` with no `checks_<name>.py` yet exits **3**, not 1.
The hooks let it through with a warning on every run rather than blocking every
edit until its questions are written.

The alternative was worse in both directions: block, and adding a payload halts
all work until its eval questions exist; treat it as a pass, and a payload can
sit unguarded forever looking exactly like one that passes. Six payloads spent
part of 2026-09-08 in that state deliberately, and said so on every commit.

The pairing itself is by name — `data/samples/foo.json` is checked by
`src/checks_foo.py`, derived rather than registered — so a payload and its
questions cannot drift apart, and adding one means editing one file.

## Rule 9 — `==` is not equality, for JSON

Python says `True == 1` and `0 == 0.0`. Both let a value change type without
changing equality, so a round-trip check written as `restored == stripped`
reports success on a document that decoded ints as floats.

A column holding `0` in some rows and `0.0` in others collapsed into `#const`
and came back all-float, and the whole-payload check said "equal" because dict
equality bottoms out in the same `==`. Nothing could see it — not the harness,
not the property test, not the round-trip guard inside `compress_json`. Found by
the PR review, and the type is user-visible: `column_type_name` grew the `num`
type precisely because int-vs-float in a numeric column shows.

`table.same_json` is the only comparison the pipeline should use. It is wired
into `compress_json`'s guard, the harness's round-trip and answer checks, and
both property-test properties — four places, because any one of them left on
`==` is a hole in the same wall.

## Rule 10 — Some things only a cold reader can check

No automated gate can tell whether a *model* reads the format correctly. The
harness runs its checks on decompressed data, so it never reads the document at
all.

The standing procedure: give the rendered document to a reader with **no access
to this repo** and ask it to answer the eval questions plus "what was ambiguous?".
Claude's own reading does not count — it designed the format.

Two runs so far, and its record is: every factual answer correct both times,
and both times it found a real defect no automated gate could have seen.

| | answers | confidence | what it found |
|---|---|---|---|
| `docs/cold-read-2026-09-08.md` | 13/13 | 6/10 | drifted across a run of empty cells |
| `docs/cold-read-2026-09-08b.md` | 16/16 | 7/10 | miscounted 13 positional columns; `json` used for a plainly numeric column; `#keyed"_key"` had no delimiter; the dotted-path convention was never stated |

The two near-misses are the same failure — counting positional values against a
header some distance above — and both were caught by the reader recounting.
"Caught by recounting" is luck about how careful the reader was, not a property
of the format, so the header now repeats every 40 rows (+0.3%).

Correctness is not the same as legibility, and only this test tells them apart.
