"""Why does the stored arm miscount a column that holds no handle?

The last open finding in seven cold reads, and the only one with no known cause.
`src/eval_model.py`'s stored arm missed Q5 (issues with zero comments) and Q6
(total comments) on 2026-09-11, and missed **the same two** on 2026-09-12 with a
different reader. Both times the same three things were verified: the `comments`
column contains no handle, it decompresses identically in the compressed and
stored arms, and the compressed and raw readers answer both correctly.

Twice is enough to stop describing it and isolate it.

## The hypothesis this was built to test — and it did not survive

In the stored document the handle sits immediately left of the column being
counted, and the handle carries a number:

    stored:      NONE,\\@0004[345t],1,2026-09-07T09:22:06Z,...
                            ^^^^   ^
                         handle    comments

    compressed:  NONE,"## Summary\\n\\nFixes #36460...",1,2026-09-07T09:22:06Z,...
                                                       ^
                                                    comments

`[345t]` is a number adjacent to the number the question asks for, once per row.
The compressed document has a long quoted body there instead, which is an obvious
boundary. The observed errors fit: totals of 32 and 33 against a truth of 31, and
one zero missed out of eleven.

If that is the cause, `[Nt]` has a cost nobody priced. It was bought at 0.5 points
across the sample set for a benefit cold read #4 then proved — a reader priced a
fetch from it without retrieving anything. A second, *negative* effect on the
column beside it would be new information about a decision already made.

## Five arms, one variable each

    compressed      the control that has passed twice        3 questions, no tool
    stored          the arm that has failed twice, as it ships
    stored-bare     the same document, `\\@0004` not `\\@0004[345t]`
    stored-full     the stored document, the sweep's 15 questions, still no tool
    stored-fetch    the same again WITH the fetch tool — the sweep, reproduced

`stored-bare` is produced by `render(parse(stored))` — the shipped reader and the
shipped writer, not a third renderer. `decode_cell` keeps only the id and drops
the decoration, because the decoration is for the reader and not the parser
(render.py), so re-rendering the parsed table emits bare handles and changes
nothing else in the document. Verified by `--dry-run`, which also checks that the
comments column is identical in every arm.

**Which cells are stored is deliberately NOT a variable.** `stash_bulk_cells`
prices each cell against the handle it would really get, `[Nt]` and all, so every
arm holds the same 32 stored cells and they differ only in how the handle is
written. Same "one variable at a time" rule `measure_store.py` states.

The last two arms exist because the first three did not have that discipline.
They dropped the fetch tool AND cut fifteen questions to three at the same time,
so a clean result could not say which change mattered — the one-variable sin this
file preaches, committed in its own design. `stored-full` puts the questions back
without the tool; `stored-fetch` puts the tool back too and is the sweep exactly.

## What it found, 2026-09-12: the hypothesis above is WRONG

Twenty-five readers. **The four arms that could not fetch missed nothing at all**
— 20 for 20, including every reader listing all thirty comments values correctly.
The stored document, byte for byte the one that failed twice, is not a sufficient
cause, and neither is the size of the task.

**Every miss in the run was in `stored-fetch`**: one reader of five, which read
row 7 as 0 where the document says 1 and reported 12 zeros and a total of 30.
D1 is what shows that — it is a misread row, not bad arithmetic — and that is the
distinction reads #5 and #7 structurally could not make.

So the evidence points at the fetch tool, or at what having fetched leaves in the
reader's context. **It is not a rate and this file does not call it a cause:**
1 in 5 is below the 3 the bar below demands, and the write-up says suspect.

`[Nt]` is cleared. It was priced at 0.5 points for a benefit cold read #4 proved,
and the second, negative effect guessed at above does not show up.

## Why this is a script and not a gate

Non-deterministic and it needs readers — two of the three reasons `eval_model.py`
is not a gate either (Rule 12). It lives with `src/measure_*.py`, "one-off
measurement scripts, nothing runs them but you". No hook calls it.

Usage:
    python src/measure_miscount.py --dry-run          # build and check the arms
    python src/measure_miscount.py --emit DIR         # 25 reading tasks
    python src/measure_miscount.py --grade DIR        # score them against the bar
"""

import argparse
import json
import os
import sys

import checks_github_issues as checks
import eval_model
import render
import store as store_module
from compress import compress_json
from table import same_json
from tokens import token_count

SAMPLE = "data/samples/github_issues.json"

# Readers per arm. Five rather than one because one per arm is exactly what left
# this ambiguous twice, and rather than fifty because these are read by hand-
# launched subagents. It is a screening experiment: enough to point at a cause,
# not enough to measure the size of one. Every write-up has to say so.
READERS_PER_ARM = 5

ARMS = ("compressed", "stored", "stored-bare", "stored-full", "stored-fetch")

# The first three arms ask three questions. The sweep that produced the finding
# asked fifteen, so "no fetch tool" and "a much shorter task" moved together and
# a clean run of the first three cannot tell them apart — the same one-variable
# sin this file preaches against, committed in the experiment's own design.
#
# `stored-full` puts back the sweep's whole question set on the same document
# and still offers no fetch tool. It is the only arm whose extra questions are
# not scored: they are there to restore the working conditions, not to be
# answered well. Q5, Q6 and D1 are still what decides anything.
FULL_ARM = "stored-full"

# And `stored-fetch` is the sweep itself: the same document, the same fifteen
# questions, and the fetch tool put back. It was added after the first four arms
# came back 20/20 and left exactly one difference from the run that found the
# miscount. An experiment that narrows a cause to one suspect and then stops
# short of testing it has not finished.
#
# Q5, Q6 and D1 still need no fetch here. If this arm misses them, what did it is
# something about having retrieved — the content that lands in the reader's
# context, or the turns spent getting it — and not the document, which four arms
# have now cleared.
FETCH_ARM = "stored-fetch"

# The bar, written here rather than decided when the numbers come back, because
# "a measurement whose threshold is chosen afterwards is not a measurement"
# (eval_model.py). All three conditions are about MISS counts out of
# READERS_PER_ARM, where a reader misses if it gets Q5 or Q6 wrong.
#
# The control condition exists for the reason RAW_MIN_GRADED_FRACTION exists: a
# comparison against a reference that has itself collapsed is not a comparison.
MIN_STORED_MISSES = 3      # below this the phenomenon did not reproduce
MAX_CONTROL_MISSES = 1     # above this the run measured the readers, not the format
MAX_BARE_MISSES = 1        # at or below this, with stored >= MIN, [Nt] is implicated


def questions_and_truth(data):
    """The three questions, and the answers computed from the raw payload.

    Q5 and Q6 come from `checks_github_issues` rather than being rewritten here:
    they are the two that actually failed, and a second copy of them could
    disagree with the harness about what the right answer is (Rule 4's shape).

    D1 is new and is the point of the exercise. Reads #5 and #7 could see only
    that a total had moved; asking for the column itself says WHICH row was
    misread, which separates a reading failure from an arithmetic one.
    """
    truth = {
        "Q5": checks.q5_zero_comment_count(data),
        "Q6": checks.q6_total_comments(data),
        "D1": [issue["comments"] for issue in data],
    }
    asked = {
        "Q5": "How many entries have exactly zero comments? Answer with a number.",
        "Q6": "What is the total number of comments across all entries? Answer "
              "with a number.",
        "D1": "List the value of the comments column for every entry, in the "
              "order the entries appear in the document. Answer as an array of "
              f"{len(data)} numbers.",
    }
    return asked, truth


def full_question_set(asked):
    """The sweep's whole question set, for the arm that restores its conditions.

    Taken from `checks_github_issues.ASK` rather than retyped, so the arm really
    is asking what the run that found the miscount asked. Q5 and Q6 come from
    there too and are overwritten with the wording above only so that all four
    arms read the identical sentence for the two questions that decide anything.
    """
    questions = dict(checks.ASK)
    questions.update(asked)
    return questions


def build_arms(path):
    """The three documents, from one payload and one store.

    The stored document is built with a real FileStore, never MemoryStore
    (Rule 15) — even though nothing here fetches, because the document a reader
    sees should be the one the tool really writes.
    """
    with open(path, encoding="utf-8") as handle:
        raw_text = handle.read()
    data = json.loads(raw_text)

    root = os.path.join(os.path.dirname(os.path.abspath(path)), ".miscount-store")
    backing = store_module.FileStore(root)

    compressed, _ = compress_json(data, original_text=raw_text)
    stored, _ = compress_json(data, original_text=raw_text, store=backing)

    # The shipped reader, then the shipped writer. parse() keeps a handle's id
    # and drops everything after it, so this re-emits the same table with bare
    # handles and touches nothing else.
    bare = render.render(render.parse(stored))

    # ...except the legend, which still promises a form the bare document does
    # not use. Leaving it would confound "no size on the handle" with "the legend
    # describes something absent", and the second is a worse document than either
    # arm is meant to be.
    bare = bare.replace(
        r"\@nnnn[Nt] = this value is NOT in this document; N is what it costs "
        r"in tokens.",
        r"\@nnnn = this value is NOT in this document.",
    )

    # stored-full and stored-fetch are the same bytes as stored. What differs is
    # the question set and whether a fetch tool is offered, not the document —
    # that is the whole point of both arms.
    return data, {"compressed": compressed, "stored": stored, "stored-bare": bare,
                  FULL_ARM: stored, FETCH_ARM: stored}, backing.root


def comments_column(document):
    """The comments column as a reader sees it, or None if it is not readable.

    Used by --dry-run to show that the column under test really is present and
    identical in all three arms — the claim the whole experiment rests on, and
    one checked by hand every time it has come up so far.

    Read through `render.parse` rather than by splitting on newlines: a `str`
    cell may legally contain them, and the compressed document's bodies do, so
    line-splitting tears rows apart. The shipped reader is the only thing that
    knows where a row ends.
    """
    table = render.parse(document)
    if table is None:
        return None
    position = next((n for n, column in enumerate(table["columns"])
                     if column["name"] == "comments"), None)
    if position is None:
        return None
    return [row[position] for row in table["cells"]]


TASK_HEADER = """You are answering questions about one document.

Answer ONLY from the document below. Do not open any file and do not look
anything up — everything needed is either in the document or genuinely absent
from it. If the document does not contain what a question asks for, say so
instead of guessing or inferring it from neighbouring values.

Reply with a single JSON object mapping each question id to your answer, and
nothing else:

    {"Q5": 7, "Q6": 20, "D1": [0, 1, 2]}

Match the answer shape each question asks for exactly.
"""

# Only the fetch arm is told about _fetches, because only the fetch arm can run
# anything. eval_model learned the same thing the hard way: without asking, a run
# reports that nobody fetched while a reader had.
FETCH_COUNT_NOTE = """
Also include the key "_fetches" with the number of times you ran the command
below (0 if you never did).
"""


def emit(out_dir):
    """One self-contained task per arm per reader: the document and the
    questions, and nothing else — no ground truth, no repo path, and not which
    arm it is. The same exclusions eval_model.emit makes, for the same reason."""
    data, arms, store_root = build_arms(SAMPLE)
    asked, truth = questions_and_truth(data)

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "answers"), exist_ok=True)

    # The fetch arm's only tool, written the way eval_model.emit writes it and
    # importing the same two templates rather than retyping them: this arm is
    # supposed to BE the sweep, so any drift in the wording is drift in the thing
    # being reproduced. Absolute paths for the same reason eval_model uses them —
    # a relative MARGIN_STORE resolves differently depending on where the reader
    # happens to be standing.
    fetch_script = os.path.abspath(os.path.join(out_dir, "fetch.py"))
    with open(fetch_script, "w", encoding="utf-8") as handle:
        handle.write(eval_model.FETCH_SCRIPT.format(
            src=os.path.dirname(os.path.abspath(__file__)),
            store=os.path.abspath(store_root)))
    fetch_instructions = eval_model.FETCH_INSTRUCTIONS.format(
        python=sys.executable, script=fetch_script)

    short_body = "\n".join(f"{key}. {text}" for key, text in asked.items())
    full_body = "\n".join(f"{key}. {text}"
                          for key, text in full_question_set(asked).items())

    written = []
    for arm in ARMS:
        body = short_body if arm not in (FULL_ARM, FETCH_ARM) else full_body
        for replicate in range(1, READERS_PER_ARM + 1):
            name = f"{arm}__{replicate}"
            path = os.path.join(out_dir, name + ".md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(TASK_HEADER)
                if arm == FETCH_ARM:
                    handle.write(FETCH_COUNT_NOTE)
                    handle.write(fetch_instructions)
                handle.write("\n--- BEGIN DOCUMENT ---\n\n")
                handle.write(arms[arm])
                handle.write("\n\n--- END DOCUMENT ---\n\nQUESTIONS\n\n")
                handle.write(body + "\n")
            written.append(name)

    truth_path = os.path.join(out_dir, "_truth.json")
    with open(truth_path, "w", encoding="utf-8") as handle:
        json.dump(truth, handle, indent=2)

    print(f"{len(written)} reading task(s) in {out_dir}")
    for arm in ARMS:
        count = (len(asked) if arm not in (FULL_ARM, FETCH_ARM)
                 else len(full_question_set(asked)))
        tool = "fetch" if arm == FETCH_ARM else ""
        print(f"  {arm:<14} x{READERS_PER_ARM}  "
              f"{token_count(arms[arm]):>7,} tokens  {count:>2} questions  {tool}")
    print(f"\nanswer key: {truth_path} — never show this to whatever reads the tasks")
    print(f"answers go in {os.path.join(out_dir, 'answers')}/<arm>__<n>.json")
    return 0


def grade(out_dir):
    """Score the answers and apply the bar stated at the top of this file."""
    with open(os.path.join(out_dir, "_truth.json"), encoding="utf-8") as handle:
        truth = json.load(handle)

    misses = {}
    print(f"{'arm':<14}{'reader':>7}{'Q5':>8}{'Q6':>8}{'D1':>8}")
    for arm in ARMS:
        misses[arm] = 0
        for replicate in range(1, READERS_PER_ARM + 1):
            path = os.path.join(out_dir, "answers", f"{arm}__{replicate}.json")
            if not os.path.exists(path):
                print(f"{arm:<14}{replicate:>7}{'no answer file':>26}")
                continue
            with open(path, encoding="utf-8") as handle:
                given = json.load(handle)

            verdicts = {key: same_json(truth[key], given.get(key))
                        for key in ("Q5", "Q6", "D1")}
            # A reader "misses" on the two questions the sweep scored. D1 is
            # reported beside them but does not decide the bar: it is the
            # diagnostic that says WHY, and folding it into the count would let
            # a reader that read every row correctly and added wrong look the
            # same as one that misread a row.
            if not (verdicts["Q5"] and verdicts["Q6"]):
                misses[arm] += 1
            print(f"{arm:<14}{replicate:>7}"
                  + "".join(f"{'ok' if verdicts[k] else 'MISS':>8}"
                            for k in ("Q5", "Q6", "D1")))

    print()
    for arm in ARMS:
        print(f"  {arm:<14} {misses[arm]}/{READERS_PER_ARM} reader(s) missed Q5 or Q6")

    print()
    if misses["compressed"] > MAX_CONTROL_MISSES:
        print(f"INCONCLUSIVE — the control missed {misses['compressed']}, over "
              f"{MAX_CONTROL_MISSES}. This run measured the readers, not the format.")
        return 2
    if misses["stored"] < MIN_STORED_MISSES:
        print(f"THE DOCUMENT IS NOT A SUFFICIENT CAUSE — stored missed only "
              f"{misses['stored']}, under {MIN_STORED_MISSES}. The same bytes, "
              f"the same column and the same two questions that failed twice in "
              f"the sweep were read correctly here.")
        if misses[FULL_ARM] >= MIN_STORED_MISSES:
            print(f"  stored-full missed {misses[FULL_ARM]}/{READERS_PER_ARM}: "
                  f"restoring the sweep's fifteen questions brings the miss back "
                  f"WITHOUT a fetch tool, so the cause is the size of the task, "
                  f"not the tool and not the document.")
        else:
            print(f"  stored-full missed {misses[FULL_ARM]}/{READERS_PER_ARM} "
                  f"with the sweep's full question set and no fetch tool, so "
                  f"task size is not it either.")
        print("  [Nt] is not implicated either way: stored-bare missed "
              f"{misses['stored-bare']}/{READERS_PER_ARM}, and with stored at "
              f"{misses['stored']} there was nothing for it to separate.")

        fetch_free = sum(misses[arm] for arm in ARMS if arm != FETCH_ARM)
        print()
        if misses[FETCH_ARM] and not fetch_free:
            print(f"  EVERY MISS IN THIS RUN IS IN THE FETCH ARM: "
                  f"{misses[FETCH_ARM]}/{READERS_PER_ARM} there against 0 in the "
                  f"{len(ARMS) - 1} arms that could not fetch "
                  f"({(len(ARMS) - 1) * READERS_PER_ARM} readers).")
            print("  That is where the evidence points and it is NOT a rate: "
                  f"{misses[FETCH_ARM]} of {READERS_PER_ARM} is below the "
                  f"{MIN_STORED_MISSES} this file set in advance for calling "
                  "something reproduced. Suspect, not cause.")
        elif not misses[FETCH_ARM]:
            print("  The fetch arm missed nothing either. Nothing in this run "
                  "reproduces the sweep's finding, which makes the sweep's two "
                  "misses reader variance until something reproduces them.")
        return 2
    if misses["stored-bare"] <= MAX_BARE_MISSES:
        print(f"[Nt] IS IMPLICATED — stored missed {misses['stored']}, "
              f"stored-bare missed {misses['stored-bare']}.")
        print("  Screening evidence, not an effect size. A fix is a separate "
              "decision and owes its own re-measure and cold read.")
        return 0
    print(f"[Nt] IS NOT IMPLICATED — stored missed {misses['stored']} and "
          f"stored-bare missed {misses['stored-bare']}; removing the size did not "
          f"separate them.")
    print("  The document is still implicated (the control passed and no arm "
          "could fetch). Next hypothesis, not improvised here: handle adjacency "
          "itself rather than the size it carries.")
    return 0


def dry_run():
    """Build the arms and check the claims the experiment rests on. No readers."""
    data, arms, _ = build_arms(SAMPLE)
    asked, truth = questions_and_truth(data)

    print(f"{'arm':<14}{'tokens':>9}{'handle form':>22}")
    for arm in ARMS:
        handle = next((field for line in arms[arm].split("\n")
                       for field in line.split(",")
                       if field.startswith(render.HANDLE_PREFIX)), "—")
        print(f"{arm:<14}{token_count(arms[arm]):>9,}{handle:>22}")

    print()
    for arm in ARMS:
        ok = same_json(comments_column(arms[arm]), truth["D1"])
        print(f"  comments column in {arm:<14} "
              f"{'identical to the raw payload' if ok else 'DIFFERS — stop'}")

    print()
    print(f"  stored vs stored-bare differ only in handles: "
          f"{_differs_only_in_handles(arms['stored'], arms['stored-bare'])}")
    print(f"  truth: Q5={truth['Q5']}  Q6={truth['Q6']}  "
          f"D1={len(truth['D1'])} values")
    print()
    for key, text in asked.items():
        print(f"  {key}: {text}")
    return 0


def _differs_only_in_handles(sized, bare):
    """True if every cell that differs between the two documents is a handle in
    both, and every non-row line is identical apart from the legend.

    The experiment's whole claim is that exactly one variable moved. Checked
    rather than asserted, because `render(parse(x))` is a round trip through two
    functions and "it only dropped the size" is the kind of thing that stays true
    right up until some other encoding changes.

    Compared through the parsed tables for the same reason comments_column is:
    a cell may contain a newline, so the documents cannot be diffed by line.
    """
    left_table = render.parse(sized)
    right_table = render.parse(bare)
    if left_table is None or right_table is None:
        return False

    if [c["name"] for c in left_table["columns"]] != \
            [c["name"] for c in right_table["columns"]]:
        return False
    if len(left_table["cells"]) != len(right_table["cells"]):
        return False

    for left_row, right_row in zip(left_table["cells"], right_table["cells"]):
        for one, other in zip(left_row, right_row):
            if isinstance(one, render.Handle) and isinstance(other, render.Handle):
                if one.cell_id != other.cell_id:
                    return False
                continue
            if not same_json(one, other):
                return False

    # Everything outside the rows: the #store line, #const, #dict, #path. The
    # legend is excluded because build_arms rewrites it on purpose.
    def preamble(document):
        return [line for line in document.split("\n")
                if line.startswith("#") and not line.startswith("#legend")]

    return preamble(sized) == preamble(bare)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true",
                       help="build the arms and check them; no readers, no spend")
    group.add_argument("--emit", metavar="DIR", help="write the reading tasks")
    group.add_argument("--grade", metavar="DIR", help="score the answers")
    args = parser.parse_args(argv)

    if args.dry_run:
        return dry_run()
    if args.emit:
        return emit(args.emit)
    return grade(args.grade)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
