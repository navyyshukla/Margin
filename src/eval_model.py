"""Does a model still answer correctly when it reads a compressed document?

Every other number in this repo is about **prompt size**. This is the only thing
here that measures the other half of the project's one-line promise — "without
degrading answer quality" — and until it existed, 41.6% and 73.7% were claims
about prompts and not about answers.

## Why this is a script and not an eighth gate

It is non-deterministic, it needs a network, and it costs money. Each of those on
its own is a reason a gate gets bypassed, and a bypassed gate reports green while
guarding nothing (Rule 12). So it lives with src/measure_*.py — "nothing runs
them but you" — and no hook calls it. The seven gates still answer "is the
document correct?"; this answers "is the document *readable*?", which is a
different question and deserves a different cadence.

## Three arms, because two would leave the older claim untested

    raw         the payload exactly as fetched            no tools
    compressed  compress_json(data)                       no tools
    stored      compress_json(data, store=FileStore(...)) the `fetch` tool

`raw` is the reference. Building this only for the store would have left the
41.6% compressor — shipped four PRs earlier — permanently unmeasured.

The stored arm uses a real FileStore, never MemoryStore. Rule 15 exists because
every store check ran on MemoryStore and FileStore shipped broken.

## Two kinds of question, and only one needs a judge

  GRADED (72)  the existing PRESERVE_CHECKS and REMOVED_CHECKS. Ground truth is
               computed from the raw payload at run time, exactly as
               eval_harness.py does it, so grading is a same_json comparison and
               **no second model is involved**. This is also what finally makes
               those checks non-tautological: eval_harness.py answers them with
               the same Python function on both sides of an equality it already
               asserted (Rule 16); here a model answers them by reading.
  JUDGED (16)  the MANUAL_QUESTIONS, which until now printed "----" and were
               checked by nobody. Judged against the raw arm's own answer,
               because the claim under test is that answers do not MOVE.

Usage:
    python src/eval_model.py --dry-run          # no calls, no spend; prints the bill
    python src/eval_model.py --pilot            # one payload, all three arms
    python src/eval_model.py                    # the full sweep
    python src/eval_model.py --regrade data/eval_runs/<file>.json
"""

import argparse
import datetime
import importlib
import json
import os
import sys
import tempfile

import mcp_server
import render
import store as store_module
from compress import compress_json
from detect import detect_content_type
from table import same_json
from tokens import token_count

# --- what answers, what judges -------------------------------------------

# Pinned, and recorded into every run file, because a run is only comparable to
# another run by the same pair. Verified against Google's model list 2026-09-11:
# gemini-3.8-flash is the current stable Flash (released 2026-09-02).
ANSWER_MODEL = "gemini-3.8-flash"

# The judge is deliberately stronger than the answerer: a weak judge turns a
# 16-question sample into noise, and the judged half has no ground truth to fall
# back on. It is a PREVIEW model by explicit choice — the stronger judge today,
# against the risk that a Google-side change makes a future run non-comparable.
# That risk is why both ids are stamped into the run file rather than assumed.
JUDGE_MODEL = "gemini-3.1-pro-preview"

# Prices per 1M tokens, from ai.google.dev/gemini-api/docs/pricing on 2026-09-11.
# Only used for the --dry-run estimate, so being a little stale costs nothing but
# a wrong estimate — but it is dated for the same reason every number here is.
PRICES = {
    ANSWER_MODEL: {"in": 0.75, "out": 3.75, "cached_in": 0.075},
    JUDGE_MODEL: {"in": 2.00, "out": 12.00, "cached_in": 0.20},
}

# --- the bar, written before the first run -------------------------------

# Stated here, in the source, rather than decided when the numbers come back.
# A measurement whose threshold is chosen afterwards is not a measurement.
#
# GRADED is exact: ground truth is computed, so any gap between an arm and raw is
# a real difference and not judge noise. JUDGED allows one, because an LLM judge
# over 16 questions cannot resolve better than that and pretending otherwise
# would put a decision on top of noise.
GRADED_ALLOWED_GAP = 0
JUDGED_ALLOWED_GAP = 1

# The reference has to be worth referring to.
#
# Both thresholds above are RELATIVE to the raw arm, and a relative test against
# a broken reference passes vacuously: the first stub run of this file scored
# 0/11 on every arm and printed "within the stated thresholds", because zero
# minus zero is zero. That is the same failure Rule 1 records — a check that
# cannot fail because the thing it compares against already collapsed — and it
# would have been indistinguishable from a real pass in the run file.
#
# So the raw arm must clear this floor before any gap is interpreted, and a run
# below it is INCONCLUSIVE rather than passing. 0.8 is a judgement, not a
# measurement: high enough that a broken harness, a bad key or a model that
# cannot read JSON is caught, low enough that a few genuinely hard questions do
# not invalidate a sweep. It is the one number here chosen rather than derived,
# and it is only ever used to refuse to draw a conclusion.
RAW_MIN_GRADED_FRACTION = 0.8

# How many times the stored arm may call `fetch` for one question before we stop
# it. A model that keeps fetching is a finding, not a crash — MAX_RESPONSE_TOKENS
# already truncates rather than defers precisely to avoid a fetch loop
# (mutation_test.py has a named mutation for it), so hitting this ceiling means
# that protection failed and the run should say so.
MAX_TOOL_TURNS = 6

# Checks with no ASK entry, and why. Printed every run: a question that is not
# asked has to be visible, or coverage quietly rots (Rule 2).
NOT_ASKED = {
    "X6": "answer is the whole 166-rate table (2,876 chars) — transcription, not comprehension",
    "X7": "answer is all 166 currency codes (1,162 chars) — same",
    "O6": "answer is all 168 temperatures (1,005 chars) — same",
    "P8": "answer is all 46 game versions (603 chars) — same; P13 asks it in prose instead",
    "C6": "asks what Python type a value decoded to — a question about the decoder, not the data",
}


def checks_for(sample_path):
    """The questions paired to this payload, by filename. Same rule as
    eval_harness.checks_module_name, and for the same reason: naming is the
    pairing, so a payload and its questions cannot drift apart."""
    name = os.path.basename(sample_path).rsplit(".", 1)[0]
    return importlib.import_module("checks_" + name)


def as_json_value(value):
    """Ground truth put through the same JSON round-trip the answer takes.

    Several checks return tuples — q4_pr_vs_plain_counts is [24, 6] as a tuple —
    and JSON has no tuple. Without this, every tuple-valued question fails on a
    difference introduced by the transport rather than by the compression, and
    the run measures our plumbing.
    """
    return json.loads(json.dumps(value, default=str))


# --- building the three prompts ------------------------------------------

# One preamble for every arm. Deliberately: the arms must differ in the DOCUMENT
# and in whether a tool exists, and in nothing else, or the comparison measures
# the wording instead. In particular this does not explain \@nnnn handles — the
# document's own legend does, and whether that is enough is exactly what cold
# read #4 tested and what the stored arm is measuring here.
PREAMBLE = (
    "Answer the question using only the data below. If the data does not contain "
    "what is needed to answer, say so plainly rather than guessing or inferring "
    "it from surrounding values.\n\n"
)


def build_arms(sample_path, store_root):
    """The same payload as the reader would see it, three ways.

    Returns {arm: {"text", "identical_to", "doc_has_store"}}. `identical_to`
    names an arm this one is byte-for-byte equal to, which happens constantly and
    matters twice: five of eight payloads carry no #store line at all, and two
    come back from the compressor unchanged. Asking the same bytes twice cannot
    produce a different answer, so those arms are reported as equal by
    construction and never sent. That is most of the cost of a sweep.
    """
    raw_text = open(sample_path, encoding="utf-8").read()
    content_type, data = detect_content_type(raw_text)
    if content_type != "json":
        raise SystemExit(f"{sample_path} is {content_type}, not json")

    compressed_text, _ = compress_json(data, original_text=raw_text)

    backing = store_module.FileStore(store_root)
    stored_text, _ = compress_json(data, original_text=raw_text, store=backing)

    arms = {
        "raw": {"text": raw_text, "identical_to": None},
        "compressed": {"text": compressed_text, "identical_to": None},
        "stored": {"text": stored_text, "identical_to": None},
    }
    if compressed_text == raw_text:
        arms["compressed"]["identical_to"] = "raw"
    if stored_text == compressed_text:
        arms["stored"]["identical_to"] = "compressed"
    elif stored_text == raw_text:
        arms["stored"]["identical_to"] = "raw"

    for arm in arms.values():
        arm["doc_has_store"] = render.STORE_PREFIX in arm["text"]
    return arms


FETCH_TOOL = {
    "type": "function",
    "name": "fetch",
    # The description the MCP server actually ships, imported rather than
    # retyped. A tool described differently here would make this a measurement
    # of a prompt Margin does not ship (the src/render.py principle).
    "description": mcp_server.FETCH_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "document": {"type": "string",
                         "description": "the id on the document's #store line"},
            "ids": {"type": "array", "items": {"type": "string"},
                    "description": "handle numbers, e.g. ['0001','0004']"},
            "query": {"type": "string",
                      "description": "optional: return only matching parts"},
        },
        "required": ["document", "ids"],
    },
}

# The answer comes back as a JSON *string* to be parsed, rather than as a
# free-form typed field. The 72 graded answers are numbers, strings, booleans,
# arrays and objects, and one schema cannot say "any of those" in a way every
# schema dialect agrees on. A string always validates; json.loads then does the
# typing, and a parse failure is recorded as a wrong answer with the raw text
# kept, rather than crashing the sweep.
GRADED_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": {
        "type": "object",
        "properties": {
            "answer_json": {
                "type": "string",
                "description": "your answer encoded as a JSON value: a number, a "
                               "quoted string, true/false, an array, or an object",
            }
        },
        "required": ["answer_json"],
    },
}

JUDGE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": {
        "type": "object",
        "properties": {
            "same": {"type": "boolean"},
            "why": {"type": "string"},
        },
        "required": ["same", "why"],
    },
}


def ask(client, document_text, question, graded, tools=None):
    """One question against one document. Returns a record, never raises.

    The document goes FIRST and the question LAST, every time. That ordering is
    the only lever available: the Interactions API supports implicit caching
    only, which keys on a stable prefix, so questions about the same payload
    share their document at a tenth of the input price. Reversing these two lines
    would multiply the bill for this sweep by roughly ten.
    """
    prompt = PREAMBLE + document_text + "\n\nQUESTION: " + question

    kwargs = {"model": ANSWER_MODEL, "input": prompt}
    if graded:
        kwargs["response_format"] = GRADED_FORMAT
    if tools:
        kwargs["tools"] = tools

    record = {"question": question, "fetch_calls": [], "turns": 0}
    try:
        interaction = client.interactions.create(**kwargs)
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    # The tool loop. There is no automatic function execution in this SDK, so
    # this is ours to drive — and driving it is the point of the stored arm:
    # nothing else in this repo ever exercises the model's DECISION to call
    # `fetch`, which is the entire value of the store stage.
    while record["turns"] < MAX_TOOL_TURNS:
        calls = [s for s in getattr(interaction, "steps", []) or []
                 if getattr(s, "type", None) == "function_call"]
        if not calls:
            break
        record["turns"] += 1

        results = []
        for call in calls:
            args = call.arguments
            if isinstance(args, str):
                args = json.loads(args)
            record["fetch_calls"].append(args)
            try:
                out = mcp_server.fetch(**args)
            except Exception as exc:               # noqa: BLE001
                out = f"fetch failed: {type(exc).__name__}: {exc}"
            results.append({
                "type": "function_result",
                "name": call.name,
                "call_id": call.id,
                "result": [{"type": "text", "text": out}],
            })

        follow = {"model": ANSWER_MODEL, "input": results,
                  "previous_interaction_id": interaction.id, "tools": tools}
        if graded:
            follow["response_format"] = GRADED_FORMAT
        try:
            interaction = client.interactions.create(**follow)
        except Exception as exc:                   # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {exc}"
            return record
    else:
        # Fell out of the loop still wanting to call. Recorded, not hidden: this
        # is the fetch-loop failure MAX_RESPONSE_TOKENS is supposed to prevent.
        record["hit_turn_ceiling"] = True

    record["text"] = interaction.output_text
    usage = getattr(interaction, "usage", None)
    if usage is not None:
        # total_input_tokens, not input_tokens. Checked against the installed
        # SDK's Usage model rather than assumed — the shorter names look right
        # and do not exist, and getattr would have recorded None for every
        # question without ever failing.
        record["usage"] = {
            "input": getattr(usage, "total_input_tokens", None),
            "output": getattr(usage, "total_output_tokens", None),
            "cached": getattr(usage, "total_cached_tokens", None),
        }
    return record


def parse_graded(record):
    """The answer as a JSON value, or a note saying why it is not one."""
    if "text" not in record:
        return None, record.get("error", "no response")
    try:
        outer = json.loads(record["text"])
        return json.loads(outer["answer_json"]), None
    except Exception as exc:                       # noqa: BLE001
        return None, f"unparseable answer: {type(exc).__name__}: {exc}"


def judge(client, question, reference, candidate):
    """Does `candidate` say the same thing as `reference`?

    Not "is it correct" — the claim under test is that answers do not MOVE, so
    the raw arm's own answer is the reference. If raw got something wrong and
    compressed got it wrong the same way, compression cost nothing, which is what
    this project actually promises.
    """
    prompt = (
        "Two answers were given to the same question about the same underlying "
        "data. The first was produced from the original data; the second from a "
        "compressed form of it. Decide whether the second says substantially the "
        "same thing as the first. Differences in wording, length or ordering do "
        "not matter. A missing fact, a contradicted fact, or an invented fact "
        "does.\n\n"
        f"QUESTION: {question}\n\nFIRST (reference):\n{reference}\n\n"
        f"SECOND (candidate):\n{candidate}\n"
    )
    try:
        out = client.interactions.create(
            model=JUDGE_MODEL, input=prompt, response_format=JUDGE_FORMAT)
        verdict = json.loads(out.output_text)
        return bool(verdict["same"]), verdict.get("why", "")
    except Exception as exc:                       # noqa: BLE001
        return None, f"judge failed: {type(exc).__name__}: {exc}"


# --- the sweep ------------------------------------------------------------

ARMS = ("raw", "compressed", "stored")


def run_payload(client, sample_path, store_root, arms_wanted, do_judge, out):
    """Every question on one payload, across the wanted arms."""
    module = checks_for(sample_path)
    ask_map = getattr(module, "ASK", {})
    raw_text = open(sample_path, encoding="utf-8").read()
    _, data = detect_content_type(raw_text)
    arms = build_arms(sample_path, store_root)
    name = os.path.basename(sample_path)

    graded = [(label, fn, False) for label, fn in module.PRESERVE_CHECKS]
    graded += [(label, fn, True) for label, fn in module.REMOVED_CHECKS]

    print(f"\n=== {name}")
    for arm in ARMS:
        note = f" (identical to {arms[arm]['identical_to']} — not sent)" \
            if arms[arm]["identical_to"] else ""
        print(f"    {arm:11} {token_count(arms[arm]['text']):>7,} tokens{note}")

    results = {"payload": name, "graded": [], "judged": []}

    for label, fn, removed in graded:
        key = label.split()[0]
        if key not in ask_map:
            continue
        try:
            truth = fn(data)
        except Exception as exc:                   # noqa: BLE001
            print(f"  SKIP  {label}: check failed on the raw payload: {exc}")
            continue

        row = {"label": label, "removed": removed, "arms": {}}
        for arm in arms_wanted:
            # A REMOVED check is the one question whose right answer depends on
            # the arm: the field IS in the raw payload and MUST NOT be in a
            # compressed one. Expecting the same answer from both would mark the
            # adversarial question wrong exactly when the compressor is right.
            expected = as_json_value(truth if arm == "raw" else False) if removed \
                else as_json_value(truth)

            twin = arms[arm]["identical_to"]
            if twin and twin in row["arms"]:
                row["arms"][arm] = dict(row["arms"][twin], by_construction=twin)
                continue

            rec = ask(client, arms[arm]["text"], ask_map[key], graded=True,
                      tools=[FETCH_TOOL] if arm == "stored" else None)
            answer, problem = parse_graded(rec)
            rec.update(expected=expected, answer=answer, problem=problem,
                       correct=(problem is None and same_json(expected, answer)))
            row["arms"][arm] = rec
        results["graded"].append(row)
        _print_row(label, row, arms_wanted)

    for label in module.MANUAL_QUESTIONS:
        key = label.split()[0]
        if key not in ask_map:
            continue
        row = {"label": label, "arms": {}}
        for arm in arms_wanted:
            twin = arms[arm]["identical_to"]
            if twin and twin in row["arms"]:
                row["arms"][arm] = dict(row["arms"][twin], by_construction=twin)
                continue
            row["arms"][arm] = ask(client, arms[arm]["text"], ask_map[key],
                                   graded=False,
                                   tools=[FETCH_TOOL] if arm == "stored" else None)
        if do_judge and "raw" in row["arms"]:
            reference = row["arms"]["raw"].get("text", "")
            for arm in arms_wanted:
                if arm == "raw":
                    row["arms"][arm]["same_as_raw"] = True
                    continue
                same, why = judge(client, ask_map[key], reference,
                                  row["arms"][arm].get("text", ""))
                row["arms"][arm].update(same_as_raw=same, judge_note=why)
        results["judged"].append(row)
        _print_row(label, row, arms_wanted, judged=True)

    out["payloads"].append(results)
    return results


def _mark(rec, judged):
    if rec.get("error"):
        return "ERR "
    ok = rec.get("same_as_raw") if judged else rec.get("correct")
    if ok is None:
        return "?   "
    return "ok  " if ok else "MISS"


def _print_row(label, row, arms_wanted, judged=False):
    marks = "  ".join(f"{arm[:4]}:{_mark(row['arms'][arm], judged)}"
                      for arm in arms_wanted if arm in row["arms"])
    fetches = sum(len(row["arms"][a].get("fetch_calls", []))
                  for a in row["arms"])
    tail = f"   ({fetches} fetch)" if fetches else ""
    print(f"  {label:52} {marks}{tail}")


def summarise(out, arms_wanted):
    """The verdict, against the thresholds set at the top of this file."""
    print("\n" + "=" * 72)
    tally = {arm: {"graded_ok": 0, "graded_n": 0, "judged_ok": 0, "judged_n": 0}
             for arm in arms_wanted}
    fetch_calls = 0
    for payload in out["payloads"]:
        for row in payload["graded"]:
            for arm in arms_wanted:
                rec = row["arms"].get(arm)
                if not rec:
                    continue
                tally[arm]["graded_n"] += 1
                tally[arm]["graded_ok"] += bool(rec.get("correct"))
        for row in payload["judged"]:
            for arm in arms_wanted:
                rec = row["arms"].get(arm)
                if not rec:
                    continue
                fetch_calls += len(rec.get("fetch_calls", []))
                if rec.get("same_as_raw") is None:
                    continue
                tally[arm]["judged_n"] += 1
                tally[arm]["judged_ok"] += bool(rec.get("same_as_raw"))
        for row in payload["graded"]:
            for arm in arms_wanted:
                fetch_calls += len(row["arms"].get(arm, {}).get("fetch_calls", []))

    print(f"{'arm':12} {'GRADED':>12}  {'JUDGED':>12}")
    for arm in arms_wanted:
        t = tally[arm]
        print(f"{arm:12} {t['graded_ok']:>5}/{t['graded_n']:<6} "
              f"{t['judged_ok']:>5}/{t['judged_n']:<6}")

    out["tally"] = tally
    if "raw" not in tally:
        print("\nNo raw arm in this run — nothing to compare against.")
        return 0

    print()
    raw = tally["raw"]
    floor = raw["graded_n"] * RAW_MIN_GRADED_FRACTION
    if raw["graded_n"] and raw["graded_ok"] < floor:
        # Refusing to draw a conclusion is the result here. Everything below
        # compares against this arm, so if it cannot answer its own questions
        # from the uncompressed payload, the gaps measure nothing.
        print(f"  INCONCLUSIVE: the raw arm scored {raw['graded_ok']}/"
              f"{raw['graded_n']}, under the {RAW_MIN_GRADED_FRACTION:.0%} floor.")
        print("  Every threshold below is relative to raw, so a weak reference "
              "makes them meaningless rather than passing.")
        print("  Look at the run file: this is usually a harness fault — an "
              "unparseable answer shape, a bad key, a model refusing — and not "
              "evidence about compression.")
        return 2

    failed = False
    for arm in arms_wanted:
        if arm == "raw":
            continue
        g = tally["raw"]["graded_ok"] - tally[arm]["graded_ok"]
        j = tally["raw"]["judged_ok"] - tally[arm]["judged_ok"]
        g_ok, j_ok = g <= GRADED_ALLOWED_GAP, j <= JUDGED_ALLOWED_GAP
        failed |= not (g_ok and j_ok)
        print(f"  {arm:11} GRADED gap {g:+d} (allowed {GRADED_ALLOWED_GAP}) "
              f"{'PASS' if g_ok else 'FAIL'}   "
              f"JUDGED gap {j:+d} (allowed {JUDGED_ALLOWED_GAP}) "
              f"{'PASS' if j_ok else 'FAIL'}")

    if "stored" in arms_wanted:
        # Rule 1's shape. An arm that answered without ever calling the tool is
        # not measuring the store — it either had no handles or read around them,
        # and either way the number it produced means something else.
        print(f"\n  the stored arm called fetch {fetch_calls} time(s)")
        if not fetch_calls:
            print("  WARNING: it never called fetch. Whatever this run measured, "
                  "it was not retrieval.")
    print("\n" + ("THRESHOLDS NOT MET" if failed else "within the stated thresholds"))
    return 1 if failed else 0


def estimate(paths, arms_wanted, do_judge):
    """What a sweep would cost, without making a call.

    tiktoken's cl100k counts are a proxy for Gemini's tokenizer — the right
    order, not the right number — and this says so rather than implying a
    precision it does not have.
    """
    total_in = total_out = 0
    print(f"{'payload':30} {'arm':11} {'questions':>9} {'in-tokens':>12}")
    with tempfile.TemporaryDirectory() as root:
        for path in paths:
            module = checks_for(path)
            ask_map = getattr(module, "ASK", {})
            n_graded = sum(1 for label, _ in
                           list(module.PRESERVE_CHECKS) + list(module.REMOVED_CHECKS)
                           if label.split()[0] in ask_map)
            n_judged = sum(1 for label in module.MANUAL_QUESTIONS
                           if label.split()[0] in ask_map)
            arms = build_arms(path, root)
            for arm in arms_wanted:
                if arms[arm]["identical_to"]:
                    print(f"{os.path.basename(path):30} {arm:11} "
                          f"{'—':>9} {'(identical, not sent)':>12}")
                    continue
                n = n_graded + n_judged
                per = token_count(PREAMBLE + arms[arm]["text"])
                total_in += per * n
                total_out += 200 * n
                print(f"{os.path.basename(path):30} {arm:11} {n:>9} {per * n:>12,}")

    p = PRICES[ANSWER_MODEL]
    uncached = total_in * p["in"] / 1e6
    cached = total_in * p["cached_in"] / 1e6
    out_cost = total_out * p["out"] / 1e6
    print(f"\ninput tokens (cl100k proxy): {total_in:,}")
    print(f"  if nothing caches: ${uncached + out_cost:.2f}")
    print(f"  if everything caches after the first question: ${cached + out_cost:.2f}")
    print("The truth is between the two — implicit caching needs >=4,096 tokens "
          "and a stable prefix, which is why the document is sent before the "
          "question. Judging adds roughly $0.35 at these volumes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--payload", action="append",
                        help="sample name or path; repeatable (default: all)")
    parser.add_argument("--arm", action="append", choices=ARMS,
                        help="repeatable (default: all three)")
    parser.add_argument("--pilot", action="store_true",
                        help="github_issues only — proves the loop cheaply")
    parser.add_argument("--dry-run", action="store_true",
                        help="build every prompt, make no calls, print the bill")
    parser.add_argument("--no-judge", action="store_true",
                        help="skip the judged questions (they cost the Pro model)")
    parser.add_argument("--regrade", metavar="RUNFILE",
                        help="re-grade a saved run without spending anything")
    args = parser.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.pilot:
        paths = [os.path.join(repo, "data/samples/github_issues.json")]
    elif args.payload:
        paths = [p if os.path.sep in p
                 else os.path.join(repo, "data/samples", p if p.endswith(".json")
                                   else p + ".json")
                 for p in args.payload]
    else:
        paths = sorted(
            os.path.join(repo, "data/samples", f)
            for f in os.listdir(os.path.join(repo, "data/samples"))
            if f.endswith(".json"))

    arms_wanted = tuple(a for a in ARMS if not args.arm or a in args.arm)

    if NOT_ASKED:
        print("not asked, and why:")
        for key, why in sorted(NOT_ASKED.items()):
            print(f"  {key:4} {why}")

    if args.regrade:
        print("\n--regrade is not implemented yet; the run file holds every "
              "response, so it is a pure function of that file when it is.")
        return 2

    if args.dry_run:
        print()
        estimate(paths, arms_wanted, not args.no_judge)
        return 0

    try:
        from google import genai
    except ImportError:
        print("\ngoogle-genai is not installed: uv pip install -r requirements.txt",
              file=sys.stderr)
        return 2
    if not os.environ.get("GEMINI_API_KEY"):
        print("\nGEMINI_API_KEY is not set. Export it, or use --dry-run.",
              file=sys.stderr)
        return 2

    client = genai.Client()
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = {
        "started": stamp,
        # Stamped, not assumed. A run is comparable only to another run by the
        # same pair, and the judge is a preview model that can move underneath us.
        "answer_model": ANSWER_MODEL,
        "judge_model": JUDGE_MODEL if not args.no_judge else None,
        "arms": list(arms_wanted),
        "thresholds": {"graded_gap": GRADED_ALLOWED_GAP,
                       "judged_gap": JUDGED_ALLOWED_GAP},
        "payloads": [],
    }

    runs_dir = os.path.join(repo, "data", "eval_runs")
    os.makedirs(runs_dir, exist_ok=True)
    run_file = os.path.join(runs_dir, stamp + ".json")

    # A real FileStore, in a temp root, and MARGIN_STORE set so that
    # mcp_server.fetch's own _open_store() finds the same one. FileStore rather
    # than MemoryStore because Rule 15 was bought by exactly that substitution;
    # temp rather than ~/.margin so a sweep leaves nothing behind.
    with tempfile.TemporaryDirectory(prefix="margin-eval-") as store_root:
        os.environ["MARGIN_STORE"] = store_root
        try:
            for path in paths:
                run_payload(client, path, store_root, arms_wanted,
                            not args.no_judge, out)
        finally:
            # Written before grading and before any summary, so a sweep that
            # dies half way through has still bought something.
            with open(run_file, "w", encoding="utf-8") as handle:
                json.dump(out, handle, indent=2, default=str)
            print(f"\nresponses written to {run_file}")

    return summarise(out, arms_wanted)


if __name__ == "__main__":
    sys.exit(main())
