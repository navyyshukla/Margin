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
import collections
import datetime
import importlib
import json
import os
import re
import sys
import tempfile
import time

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

# Requests per minute to stay under, and how many times to obey a 429 before
# giving up on a question.
#
# Measured against this account 2026-09-11: the free tier refuses the 21st
# request in a rolling minute on gemini-3.8-flash, naming the metric
# `generate_content_free_tier_requests, limit: 20` and asking for a retry ~53s
# later. 16 rather than 20 because the server's window and ours start at
# different instants, and being refused costs a round trip — the margin is
# cheaper than the retry. Override with --rpm on a paid key, where this is the
# single thing standing between a 4-minute sweep and a 20-minute one.
DEFAULT_RPM = 16
MAX_RATE_LIMIT_RETRIES = 5

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

def schema_for(expected):
    """The schema for one answer, typed from the ground truth we already hold.

    The first design asked for the answer as a JSON *string* — one schema for
    every question — and it failed on the very first real call. Told to encode
    "[DevTools Bug] Cannot remove node..." as a JSON string, the model put the
    plain text in the field instead of a quoted, escaped JSON value, so
    json.loads saw `[DevTools` and read it as a broken array. Every question
    would have come back unparseable. Asking a model to double-encode is a
    request to do something it has no reason to expect.

    Since the expected value is in hand before the question is asked, its type
    can be declared instead, and no encoding step exists to get wrong.

    bool is checked before int on purpose: in Python `isinstance(True, int)` is
    True, so the obvious order types every boolean answer as a number. Same trap
    as `True == 1`, which is why this repo compares with same_json (Rule 9).

    Objects need additionalProperties. Measured against the live API: a bare
    {"type": "object"} returns `{}` every time, because a schema with no declared
    properties says the object has none.
    """
    if isinstance(expected, bool):
        return {"type": "boolean"}
    if isinstance(expected, (int, float)):
        return {"type": "number"}
    if isinstance(expected, str):
        return {"type": "string"}
    if isinstance(expected, list):
        return {"type": "array", "items": {}}
    if isinstance(expected, dict):
        return {"type": "object", "additionalProperties": True}
    return {"type": "string"}


def graded_format(expected):
    return {
        "type": "text",
        "mime_type": "application/json",
        "schema": {
            "type": "object",
            "properties": {"answer": schema_for(expected)},
            "required": ["answer"],
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


class Pacer:
    """Keeps the sweep under the account's requests-per-minute ceiling.

    Measured against the live API 2026-09-11: the free tier allows 20 requests a
    minute on gemini-3.8-flash, as a rolling window — the 429 body names the
    metric and asks for a retry about 53 seconds later, not the next day. A
    sweep is a few hundred requests, so without pacing it spends most of its life
    being refused, and every refusal costs a round trip and a retry.

    Two halves, and both are needed. Pacing ahead of time keeps us under the
    ceiling; obeying the server's own retryDelay handles the case where its
    accounting and ours disagree, which they will — failed requests appear to
    count, so a client that retries eagerly holds its own window shut.
    """

    def __init__(self, per_minute):
        self.per_minute = per_minute
        self.sent = collections.deque()

    def wait(self):
        if not self.per_minute:
            return
        now = time.monotonic()
        while self.sent and now - self.sent[0] > 60:
            self.sent.popleft()
        if len(self.sent) >= self.per_minute:
            nap = 60 - (now - self.sent[0]) + 0.5
            if nap > 0:
                print(f"    (pacing: {nap:.0f}s to stay under "
                      f"{self.per_minute}/min)", flush=True)
                time.sleep(nap)
        self.sent.append(time.monotonic())


RETRY_DELAY = re.compile(r"retry in ([0-9.]+)s", re.I)


class QuotaExhausted(RuntimeError):
    """The account has no requests left, and waiting will not help.

    Distinct from a passing rate limit because the remedy is different and the
    cost of confusing them is the whole run. A per-minute ceiling is waited out;
    a per-DAY ceiling is not, and this key's free tier is per-day (measured
    2026-09-11 — a clean 90-second idle was still refused). Without this the
    sweep would spend MAX_RATE_LIMIT_RETRIES x ~53s discovering the same thing
    again for every one of the remaining questions, which is hours of waiting to
    learn what the first one already established.
    """



def call(client, pacer, **kwargs):
    """One API call, paced, and retried when the server says to wait.

    A 429 is not a failure here — it is the server telling us our own pacing was
    optimistic — so it is obeyed rather than reported. Anything else is raised to
    the caller, which records it against the question and carries on: one
    question that could not be answered must not cost the other 250.
    """
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        pacer.wait()
        try:
            return client.interactions.create(**kwargs)
        except Exception as exc:                   # noqa: BLE001
            text = str(exc)
            if "429" not in text and "too_many_requests" not in text:
                raise
            found = RETRY_DELAY.search(text)
            nap = min(float(found.group(1)) + 1 if found else 30, 90)
            print(f"    (rate limited; waiting {nap:.0f}s, "
                  f"attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})", flush=True)
            time.sleep(nap)
    raise QuotaExhausted(
        f"still refused after {MAX_RATE_LIMIT_RETRIES} attempts and "
        f"~{MAX_RATE_LIMIT_RETRIES * 53}s of waiting — this is a quota that "
        f"waiting does not clear")


def ask(client, pacer, document_text, question, expected=None, tools=None):
    """One question against one document. Returns a record, never raises.

    The document goes FIRST and the question LAST, every time. That ordering is
    the only lever available: the Interactions API supports implicit caching
    only, which keys on a stable prefix, so questions about the same payload
    share their document at a tenth of the input price. Reversing these two lines
    would multiply the bill for this sweep by roughly ten.
    """
    prompt = PREAMBLE + document_text + "\n\nQUESTION: " + question

    graded = expected is not None
    kwargs = {"model": ANSWER_MODEL, "input": prompt}
    if graded:
        kwargs["response_format"] = graded_format(expected)
    if tools:
        kwargs["tools"] = tools

    record = {"question": question, "fetch_calls": [], "turns": 0}
    try:
        interaction = call(client, pacer, **kwargs)
    except QuotaExhausted:
        raise                                      # ends the sweep; see main()
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
        for step in calls:                         # not `call` — that is the
            args = step.arguments                  # paced request helper above
            if isinstance(args, str):
                args = json.loads(args)
            record["fetch_calls"].append(args)
            try:
                out = mcp_server.fetch(**args)
            except Exception as exc:               # noqa: BLE001
                out = f"fetch failed: {type(exc).__name__}: {exc}"
            results.append({
                "type": "function_result",
                "name": step.name,
                "call_id": step.id,
                "result": [{"type": "text", "text": out}],
            })

        follow = {"model": ANSWER_MODEL, "input": results,
                  "previous_interaction_id": interaction.id, "tools": tools}
        if graded:
            follow["response_format"] = graded_format(expected)
        try:
            interaction = call(client, pacer, **follow)
        except QuotaExhausted:
            raise
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
        return json.loads(record["text"])["answer"], None
    except Exception as exc:                       # noqa: BLE001
        return None, f"unparseable answer: {type(exc).__name__}: {exc}"


def judge(client, pacer, question, reference, candidate):
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
        out = call(client, pacer, model=JUDGE_MODEL, input=prompt,
                   response_format=JUDGE_FORMAT)
        verdict = json.loads(out.output_text)
        return bool(verdict["same"]), verdict.get("why", "")
    except QuotaExhausted:
        # Same reasoning as ask(): a per-day quota is not waited out, and
        # swallowing it here would rediscover it once per judged question at
        # ~4.4 minutes each — hours of waiting to learn what the first one knew.
        raise
    except Exception as exc:                       # noqa: BLE001
        return None, f"judge failed: {type(exc).__name__}: {exc}"


# --- the sweep ------------------------------------------------------------

ARMS = ("raw", "compressed", "stored")


def run_payload(client, pacer, sample_path, store_root, arms_wanted, do_judge, out):
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

    # Registered before a single question is asked, and mutated in place. A run
    # that stops half way through a payload — quota, network, Ctrl-C — still has
    # every answer it paid for in the file. Appending at the end would throw away
    # the whole payload's work on the last question.
    results = {"payload": name, "graded": [], "judged": [],
               # Whether this payload's stored arm actually carries handles.
               # Five of eight do not, and without this the "never called fetch"
               # warning fires on them and reads as a defect when it is the
               # correct outcome: there was nothing to fetch.
               "stored_has_handles": arms["stored"]["doc_has_store"]}
    out["payloads"].append(results)

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
                # The twin's ANSWER carries over — identical bytes cannot
                # produce a different one — but not its expectation. A REMOVED
                # check expects True on raw and False everywhere else, so an arm
                # that is byte-identical to raw must be re-scored against its own
                # expectation: the field really is still there, because the
                # compressor removed nothing, and that has to fail. Copying the
                # twin's `correct` marked it right precisely when it was wrong.
                copied = dict(row["arms"][twin], by_construction=twin,
                              expected=expected)
                if "answer" in copied:
                    copied["correct"] = (copied.get("problem") is None
                                         and same_json(expected, copied["answer"]))
                row["arms"][arm] = copied
                continue

            rec = ask(client, pacer, arms[arm]["text"], ask_map[key],
                      expected=expected,
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
        if not do_judge:
            # --no-judge skips ASKING these, not just scoring them. It used to
            # ask all 16 and then decline to judge the answers, which read as
            # thrift and was the opposite: the flag exists to save requests, and
            # that spent ~32 of them per sweep to fill a column printed as "?".
            # Banking answers for a later judge is a real idea, but it is not
            # what a flag named --no-judge should quietly do, and on a
            # quota-capped key it spends exactly the requests the graded half
            # needs. Caught by watching a live run print "C9 ... raw:?" while
            # the account was being rate limited.
            continue
        row = {"label": label, "arms": {}}
        for arm in arms_wanted:
            twin = arms[arm]["identical_to"]
            if twin and twin in row["arms"]:
                row["arms"][arm] = dict(row["arms"][twin], by_construction=twin)
                continue
            row["arms"][arm] = ask(client, pacer, arms[arm]["text"],
                                   ask_map[key],
                                   tools=[FETCH_TOOL] if arm == "stored" else None)
        if do_judge and "raw" in row["arms"]:
            reference = row["arms"]["raw"].get("text", "")
            for arm in arms_wanted:
                if arm == "raw":
                    row["arms"][arm]["same_as_raw"] = True
                    continue
                if row["arms"][arm].get("by_construction"):
                    # Byte-identical to an arm already judged. Asking the judge
                    # whether a text matches itself spends a request from the
                    # same quota the graded half needs.
                    twin = row["arms"][arm]["by_construction"]
                    row["arms"][arm]["same_as_raw"] = \
                        row["arms"][twin].get("same_as_raw")
                    continue
                same, why = judge(client, pacer, ask_map[key], reference,
                                  row["arms"][arm].get("text", ""))
                row["arms"][arm].update(same_as_raw=same, judge_note=why)
        results["judged"].append(row)
        _print_row(label, row, arms_wanted, judged=True)

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


# --- the offline path: emit reading tasks, grade returned answers ----------
#
# The Gemini path above needs an API key with real quota. The free tier is ~20
# requests a day per model (measured 2026-09-11), which cannot reach the ~194 a
# sweep needs, and this is a personal project deliberately not funded — so it
# needs a reader it does not pay for.
#
# The sweep therefore splits in two. `--emit` writes one self-contained reading
# task per payload and arm; something reads them and writes answers back;
# `--grade` scores those answers with exactly the same ground truth, thresholds
# and summary the API path uses. What sits in the middle is not this script's
# business, which is the point: the reader is swappable.
#
# The reader this was built for is a fresh Claude subagent per task — the method
# docs/cold-reads.md has used by hand four times, now scored rather than eyeballed.
#
# **The isolation is the design, and it is not optional.** A reader that can see
# this repo can open data/samples/ and answer from the payload instead of from
# the document, and every number becomes a measurement of nothing. So a task file
# carries the document and the questions and NOTHING else: no ground truth, no
# repo path, no mention of Margin, not even which arm it is — "you are reading
# the compressed one" is itself a hint about what to look for.
#
# It is also why whoever writes these questions cannot be the reader. By the time
# the questions exist, their author knows the answers.

TASK_HEADER = """You are answering questions about one document.

Answer ONLY from the document below. Do not open any file and do not look
anything up — everything needed is either in the document or genuinely absent
from it. If the document does not contain what a question asks for, say so
instead of guessing or inferring it from neighbouring values.

Reply with a single JSON object mapping each question id to your answer, and
nothing else:

    {"Q1": "some string", "Q4": [24, 6], "Q13": false}

Match the answer shape each question asks for exactly.

If this task authorises a command below, also include the key "_fetches" with
the number of times you ran it (0 if you never did):

    {"Q1": "...", "_fetches": 2}
"""

FETCH_INSTRUCTIONS = """
Some values have been moved out of this document. Each is marked \\@nnnn[Nt],
where nnnn is its id and N is what it costs to read. To read some, run exactly:

    {python} {script} --document <the id on the #store line> --ids 0001 0002

Pass several ids in one command rather than one per command. You may add
--query <text> to get back only the matching part of a long value. This command
is the only tool you may use.
"""

FETCH_SCRIPT = '''#!/usr/bin/env python
"""Resolve handles for one reading task. The only tool that task is given."""
import argparse, os, sys
sys.path.insert(0, {src!r})
os.environ["MARGIN_STORE"] = {store!r}
import mcp_server

parser = argparse.ArgumentParser()
parser.add_argument("--document", required=True)
parser.add_argument("--ids", nargs="+", required=True)
parser.add_argument("--query")
args = parser.parse_args()
print(mcp_server.fetch(args.document, args.ids, args.query))
'''


def emit(paths, arms_wanted, out_dir, do_judge):
    """Write one reading task per payload and arm, plus the answer key.

    The key goes to _truth.json, which the reader is never pointed at. Task
    files are named <payload>__<arm>.md so a human can keep track; nothing
    inside a task file names the arm.
    """
    # Absolute, and this is not tidiness. Everything below is written INTO the
    # generated fetch.py and into the instructions handed to the reader, and the
    # whole point of the design is that the reader runs somewhere else entirely.
    # A relative --emit DIR produced a fetch script whose MARGIN_STORE pointed at
    # a path that only resolved from this directory, so the stored arm would have
    # silently measured a document whose handles cannot be read.
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    store_root = os.path.join(out_dir, "store")
    os.makedirs(store_root, exist_ok=True)
    src_dir = os.path.dirname(os.path.abspath(__file__))

    # A real FileStore on disk, not a temp one: the reader resolves handles
    # minutes or hours after emit ran, in a different process (Rule 15 — the
    # implementation that ships is FileStore).
    fetch_script = os.path.join(out_dir, "fetch.py")
    with open(fetch_script, "w", encoding="utf-8") as handle:
        handle.write(FETCH_SCRIPT.format(src=src_dir, store=store_root))

    truth = {"answer_key": {}, "emitted": datetime.datetime.now().isoformat(),
             "arms": list(arms_wanted), "tasks": [], "identical": []}
    written = []

    for path in paths:
        module = checks_for(path)
        ask_map = getattr(module, "ASK", {})
        raw_text = open(path, encoding="utf-8").read()
        _, data = detect_content_type(raw_text)
        arms = build_arms(path, store_root)
        name = os.path.basename(path)

        graded = [(label, fn, False) for label, fn in module.PRESERVE_CHECKS]
        graded += [(label, fn, True) for label, fn in module.REMOVED_CHECKS]

        for arm in arms_wanted:
            twin = arms[arm]["identical_to"]
            # Skip only if the twin is actually being emitted. `--arm raw --arm
            # stored` on a payload where stored is identical to *compressed*
            # used to skip stored and emit nothing for it, and the grade then
            # dropped the arm from the report entirely — a run that silently
            # measured fewer arms than asked for and still printed a pass.
            if twin and twin in arms_wanted:
                truth["identical"].append(
                    {"payload": name, "arm": arm, "same_as": twin})
                continue

            questions, key = [], {}
            for label, fn, removed in graded:
                qid = label.split()[0]
                if qid not in ask_map:
                    continue
                try:
                    value = fn(data)
                except Exception:                  # noqa: BLE001
                    continue
                # A REMOVED check is the one question whose right answer depends
                # on the arm: present in raw, gone everywhere else.
                expected = as_json_value(value if arm == "raw" else False) \
                    if removed else as_json_value(value)
                key[qid] = {"expected": expected, "removed": removed,
                            "label": label, "kind": "graded"}
                questions.append((qid, ask_map[qid]))

            if do_judge:
                for label in module.MANUAL_QUESTIONS:
                    qid = label.split()[0]
                    if qid in ask_map:
                        key[qid] = {"label": label, "kind": "judged"}
                        questions.append((qid, ask_map[qid]))

            body = [TASK_HEADER]
            if arms[arm]["doc_has_store"]:
                body.append(FETCH_INSTRUCTIONS.format(
                    python=sys.executable, script=fetch_script))
            body.append("\n--- BEGIN DOCUMENT ---\n")
            body.append(arms[arm]["text"])
            body.append("\n--- END DOCUMENT ---\n\nQUESTIONS\n")
            for qid, text in questions:
                body.append(f"{qid}. {text}")

            task_path = os.path.join(out_dir, f"{name[:-5]}__{arm}.md")
            with open(task_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(body) + "\n")

            truth["answer_key"][f"{name}|{arm}"] = key
            truth["tasks"].append({
                "task_file": task_path, "payload": name, "arm": arm,
                "questions": len(questions),
                "tokens": token_count(arms[arm]["text"]),
                "has_store": arms[arm]["doc_has_store"]})
            written.append((name, arm, len(questions),
                            token_count(arms[arm]["text"]),
                            arms[arm]["doc_has_store"]))

    with open(os.path.join(out_dir, "_truth.json"), "w", encoding="utf-8") as h:
        json.dump(truth, h, indent=2, default=str)

    print(f"\n{len(written)} reading task(s) in {out_dir}")
    print(f"{'payload':30} {'arm':11} {'questions':>9} {'tokens':>9}  store")
    for name, arm, n, toks, has_store in written:
        print(f"{name:30} {arm:11} {n:>9} {toks:>9,}  {'yes' if has_store else ''}")
    skipped = len(truth["identical"])
    print(f"\n{skipped} arm(s) skipped as byte-identical to one already asked.")
    print(f"answer key: {os.path.join(out_dir, '_truth.json')} — "
          f"never show this to whatever reads the tasks")
    print(f"answers go in {os.path.join(out_dir, 'answers')}/<payload>__<arm>.json")
    return 0


def grade(out_dir, arms_wanted):
    """Score whatever answers came back, with the same key and thresholds."""
    with open(os.path.join(out_dir, "_truth.json"), encoding="utf-8") as handle:
        truth = json.load(handle)
    answers_dir = os.path.join(out_dir, "answers")

    out = {"started": truth["emitted"],
           # Named so a run file never implies a model that did not run it.
           "answer_model": "(offline reader — see the task files)",
           "pinned_answer_model": ANSWER_MODEL, "answer_model_is_pinned": False,
           "judge_model": None, "arms": list(arms_wanted), "payloads": []}

    by_payload, missing = {}, []
    for task in truth["tasks"]:
        name, arm = task["payload"], task["arm"]
        answer_file = os.path.join(answers_dir, f"{name[:-5]}__{arm}.json")
        if not os.path.exists(answer_file):
            missing.append(f"{name}/{arm}")
            continue
        with open(answer_file, encoding="utf-8") as handle:
            given = json.load(handle)

        results = by_payload.setdefault(name, {
            "payload": name, "graded": [], "judged": [],
            "stored_has_handles": False})
        if arm == "stored" and task["has_store"]:
            results["stored_has_handles"] = True

        # How many times the reader actually retrieved. Rule 1 again: an arm
        # that answered without using the mechanism is not measuring it. In the
        # API path this is counted from the tool loop; here only the reader
        # knows, so it reports the number and the first run of this path had no
        # way to ask — it warned that nobody fetched while one reader had.
        fetches = given.pop("_fetches", None)
        if fetches:
            results.setdefault("fetches", 0)
            results["fetches"] += int(fetches)

        for qid, meta in truth["answer_key"][f"{name}|{arm}"].items():
            rows = results["graded"] if meta["kind"] == "graded" \
                else results["judged"]
            row = next((r for r in rows if r["label"] == meta["label"]), None)
            if row is None:
                row = {"label": meta["label"], "arms": {}}
                if meta["kind"] == "graded":
                    row["removed"] = meta["removed"]
                rows.append(row)
            got = given.get(qid)
            record = {"question": qid, "answer": got, "fetch_calls": [],
                      "text": json.dumps(got, default=str)}
            if meta["kind"] == "graded":
                record["expected"] = meta["expected"]
                record["correct"] = qid in given and same_json(
                    meta["expected"], got)
                if qid not in given:
                    record["problem"] = "not answered"
            row["arms"][arm] = record

    # Arms that were never sent because they are byte-identical to one that was:
    # copy the twin's result rather than scoring them as unanswered. Identical
    # bytes cannot produce a different answer, and counting them as misses would
    # invent a gap out of an optimisation.
    for same in truth.get("identical", []):
        results = by_payload.get(same["payload"])
        if not results:
            continue
        for rows in (results["graded"], results["judged"]):
            for row in rows:
                twin = row["arms"].get(same["same_as"])
                if twin is None or same["arm"] in row["arms"]:
                    continue
                copied = dict(twin, by_construction=same["same_as"])
                # Same correction as the API path: the answer copies, the
                # expectation does not. For a REMOVED check, an arm identical to
                # raw still contains the field and must be scored as failing to
                # remove it.
                key = truth["answer_key"].get(
                    f"{same['payload']}|{same['same_as']}", {}).get(
                        twin.get("question"), {})
                if row.get("removed") and key:
                    expected = as_json_value(
                        key["expected"] if same["arm"] == "raw" else False)
                    copied["expected"] = expected
                    copied["correct"] = same_json(expected, copied.get("answer"))
                row["arms"][same["arm"]] = copied

    out["payloads"] = list(by_payload.values())
    if missing:
        print(f"\nno answers yet for {len(missing)} task(s): "
              f"{', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}")
    if not out["payloads"]:
        print("nothing scored — write answer files first.")
        return 2

    for payload in out["payloads"]:
        print(f"\n=== {payload['payload']}")
        for row in payload["graded"]:
            _print_row(row["label"], row,
                       [a for a in arms_wanted if a in row["arms"]])

    runs_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "eval_runs")
    os.makedirs(runs_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_file = os.path.join(runs_dir, f"{stamp}-offline.json")
    with open(run_file, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, default=str)
    print(f"\nscored run written to {run_file}")

    present = tuple(a for a in arms_wanted
                    if any(a in r["arms"] for p in out["payloads"]
                           for r in p["graded"]))
    return summarise(out, present)


def inconclusive_reasons(tally, arms_wanted):
    """Every reason this run cannot support a verdict. Empty means it can.

    All three of the review findings that produced this function were the same
    mistake: a gap of zero between two numbers that were never measured. The
    guard has to be about the SHAPE of the comparison, not about one threshold,
    or the next threshold added repeats it.

    Three ways a comparison stops meaning anything:

      1. The reference cannot answer its own questions from the uncompressed
         payload — RAW_MIN_GRADED_FRACTION, which is what was here before.
      2. An arm was asked a different number of questions than raw. A missing
         answer file used to shrink raw's denominator, which dragged the floor
         down with it and turned every gap negative, i.e. a pass.
      3. An arm scored nothing at all, so it was never read. It used to
         disappear from the report rather than sink it.
    """
    reasons = []
    raw = tally["raw"]

    if not raw["graded_n"]:
        return ["the raw arm answered no graded questions at all"]

    floor = raw["graded_n"] * RAW_MIN_GRADED_FRACTION
    if raw["graded_ok"] < floor:
        reasons.append(
            f"the raw arm scored {raw['graded_ok']}/{raw['graded_n']}, under the "
            f"{RAW_MIN_GRADED_FRACTION:.0%} floor — a reference that weak makes "
            f"every gap below it meaningless rather than passing")

    for arm in arms_wanted:
        if arm == "raw":
            continue
        n = tally[arm]["graded_n"]
        if not n:
            reasons.append(f"the {arm} arm has no graded answers — it was never "
                           f"read, so its gap of zero is an absence, not a result")
        elif n != raw["graded_n"]:
            reasons.append(
                f"the {arm} arm answered {n} graded questions against raw's "
                f"{raw['graded_n']} — different denominators, so the gap between "
                f"them is not a difference in quality")
    return reasons


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
    refusals = inconclusive_reasons(tally, arms_wanted)
    if refusals:
        # Refusing to draw a conclusion IS the result. Everything below is a
        # comparison against raw, and a comparison needs two comparable things.
        print("  INCONCLUSIVE — this run cannot support a verdict:")
        for reason in refusals:
            print(f"    - {reason}")
        print("  These are harness faults, not evidence about compression: a "
              "missing answer file, an arm that was never read, a reference that "
              "could not answer its own questions.")
        return 2

    failed = False
    for arm in arms_wanted:
        if arm == "raw":
            continue
        g = tally["raw"]["graded_ok"] - tally[arm]["graded_ok"]
        g_ok = g <= GRADED_ALLOWED_GAP
        line = (f"  {arm:11} GRADED gap {g:+d} "
                f"(allowed {GRADED_ALLOWED_GAP}) {'PASS' if g_ok else 'FAIL'}")

        # The judged half only gets a verdict when it was actually judged.
        # It printed "JUDGED gap +0 (allowed 1) PASS" on every --no-judge and
        # every offline run, off a 0/0 tally — zero minus zero is zero, so the
        # bar cleared itself. That is Rule 16 exactly, in the file that added
        # Rule 16, one threshold along from the floor written to prevent it.
        if tally[arm]["judged_n"]:
            j = tally["raw"]["judged_ok"] - tally[arm]["judged_ok"]
            j_ok = j <= JUDGED_ALLOWED_GAP
            line += (f"   JUDGED gap {j:+d} (allowed {JUDGED_ALLOWED_GAP}) "
                     f"{'PASS' if j_ok else 'FAIL'}")
            failed |= not j_ok
        else:
            line += "   JUDGED not measured"
        failed |= not g_ok
        print(line)

    if "stored" in arms_wanted:
        # Rule 1's shape: assert the mechanism was used, not just that an answer
        # appeared. But "no fetches" only means something went wrong if some
        # payload in this run actually had handles to fetch — five of the eight
        # carry no #store line at all, and on those a zero is the right answer.
        # The first live run warned on a coingecko-only sweep, which was a false
        # alarm dressed as a finding.
        fetch_calls += sum(p.get("fetches", 0) for p in out["payloads"])
        with_handles = [p["payload"] for p in out["payloads"]
                        if p.get("stored_has_handles")]
        print(f"\n  the stored arm called fetch {fetch_calls} time(s)")
        if not with_handles:
            print("  (no payload in this run carries handles, so zero is correct "
                  "— this run says nothing about retrieval either way)")
        elif not fetch_calls:
            print(f"  WARNING: {len(with_handles)} payload(s) carried handles "
                  f"({', '.join(with_handles)}) and the model never fetched. "
                  "Whatever this run measured, it was not retrieval.")
    print("\n" + ("THRESHOLDS NOT MET" if failed else "within the stated thresholds"))
    return 1 if failed else 0


def _fake_run(arms):
    """A run file shaped enough for summarise(), built from {arm: (ok, n)}."""
    rows = []
    for i in range(max(n for _, n in arms.values())):
        row = {"label": f"Q{i + 1}", "removed": False, "arms": {}}
        for arm, (ok, n) in arms.items():
            if i < n:
                row["arms"][arm] = {"correct": i < ok, "fetch_calls": []}
        rows.append(row)
    return {"payloads": [{"payload": "fake.json", "graded": rows, "judged": [],
                          "stored_has_handles": False}]}


def self_test():
    """Does the verdict refuse when there is nothing to compare?

    Every case here is a real defect this file shipped. Three of them arrived in
    one review, all the same mistake in different clothes — a gap of zero between
    two numbers that were never measured — and two of those were written *after*
    Rule 16 was added to docs/harness.md for exactly that. Intending to be
    careful about it demonstrably did not work, so it is a check now (Rule 13).

    It runs in milliseconds and needs no network, no key and no payload, which is
    why the hook and pre-commit can afford it even though the sweep itself is a
    script rather than a gate.
    """
    import contextlib
    import io

    def verdict(arms):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = summarise(_fake_run(arms), tuple(arms))
        return code, buffer.getvalue()

    failures = []

    def expect(name, arms, code, must_say=None, must_not_say=None):
        got, text = verdict(arms)
        if got != code:
            failures.append(f"{name}: expected exit {code}, got {got}")
        if must_say and must_say not in text:
            failures.append(f"{name}: output never said {must_say!r}")
        if must_not_say and must_not_say in text:
            failures.append(f"{name}: output said {must_not_say!r} and must not")

    # The happy path still has to pass, or every case below proves nothing.
    expect("all arms level", {"raw": (10, 10), "compressed": (10, 10)}, 0,
           must_say="within the stated thresholds")

    # A real regression must still fail.
    expect("compressed one behind", {"raw": (10, 10), "compressed": (9, 10)}, 1,
           must_say="THRESHOLDS NOT MET")

    # The floor, which was the first of these to be written.
    expect("raw below the floor", {"raw": (5, 10), "compressed": (5, 10)}, 2,
           must_say="INCONCLUSIVE")

    # A missing answer file shrank raw's denominator, which dragged the floor
    # down with it and turned every gap negative — i.e. into a pass.
    expect("raw has fewer questions than compressed",
           {"raw": (4, 4), "compressed": (10, 10)}, 2, must_say="denominators")

    # An arm nobody read used to vanish from the report instead of sinking it.
    expect("an arm was never read", {"raw": (10, 10), "compressed": (0, 0)}, 2,
           must_say="never read")

    # The judged half cleared a bar it had never been measured against, and said
    # PASS while doing it.
    _, text = verdict({"raw": (10, 10), "compressed": (10, 10)})
    if "JUDGED not measured" not in text:
        failures.append("judged: a 0/0 tally must say 'not measured'")
    if "JUDGED gap" in text:
        failures.append("judged: a 0/0 tally must not print a gap or a verdict")

    print(f"verdict self-test: {6 - len(failures)}/6 cases hold")
    for line in failures:
        print(f"  FAIL  {line}")
    return 1 if failures else 0


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
            # Honour --no-judge, which skips asking these entirely. Counting
            # them anyway overstated the request count and the bill by 16 per
            # payload — in the one function whose only job is that number.
            n_judged = sum(1 for label in module.MANUAL_QUESTIONS
                           if label.split()[0] in ask_map) if do_judge else 0
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
    # Declared up front because argparse reads ANSWER_MODEL for --model's default
    # before the override below rebinds it. --model overrides the pinned constant
    # for this process; the run file records what actually ran and whether it was
    # the pinned model, because a run compared against a run by a different model
    # is not a comparison, and that deviation belongs in the artefact rather than
    # in somebody's shell history.
    global ANSWER_MODEL

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
                        help="do not ask or judge the 16 comprehension "
                             "questions (saves the judge model and ~32 requests)")
    parser.add_argument("--model", default=ANSWER_MODEL,
                        help=f"model that answers (default {ANSWER_MODEL}); the "
                             "run file records what actually ran")
    parser.add_argument("--rpm", type=int, default=DEFAULT_RPM,
                        help=f"requests per minute ceiling (default {DEFAULT_RPM}; "
                             "the free tier refuses the 21st in a rolling minute)")
    parser.add_argument("--self-test", action="store_true",
                        help="check that the verdict refuses when there is "
                             "nothing to compare; no network, no key, no payload")
    parser.add_argument("--emit", metavar="DIR",
                        help="write self-contained reading tasks to DIR instead "
                             "of calling an API; anything can then read them")
    parser.add_argument("--grade", metavar="DIR",
                        help="score the answers written under DIR/answers/")
    parser.add_argument("--regrade", metavar="RUNFILE",
                        help="re-grade a saved run without spending anything")
    args = parser.parse_args()

    # First, and before any path resolution: the self-test is meant to run with
    # no network, no key and no payload directory, and putting it after the
    # sample glob meant it crashed on a tree without data/samples — which is
    # every tree pre-commit builds, i.e. the one place it most needs to run.
    if args.self_test:
        return self_test()

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

    # Both offline modes come before the SDK import and the key check on
    # purpose: neither needs a network, an account or a cent, which is the
    # entire reason they exist.
    if args.emit:
        return emit(paths, arms_wanted, args.emit, not args.no_judge)
    if args.grade:
        return grade(args.grade, arms_wanted)

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

    if args.model != ANSWER_MODEL:
        print(f"\nNOTE: answering with {args.model}, not the pinned "
              f"{ANSWER_MODEL}. Recorded in the run file.")
        ANSWER_MODEL = args.model

    client = genai.Client()
    pacer = Pacer(args.rpm)
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = {
        "started": stamp,
        # Stamped, not assumed. A run is comparable only to another run by the
        # same pair, and the judge is a preview model that can move underneath us.
        "answer_model": ANSWER_MODEL,
        "pinned_answer_model": parser.get_default("model"),
        "answer_model_is_pinned": ANSWER_MODEL == parser.get_default("model"),
        "judge_model": JUDGE_MODEL if not args.no_judge else None,
        "arms": list(arms_wanted),
        "rpm": args.rpm,
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
                run_payload(client, pacer, path, store_root, arms_wanted,
                            not args.no_judge, out)
        except QuotaExhausted as exc:
            # Stop the whole sweep rather than rediscovering this per question.
            out["stopped_early"] = str(exc)
            print(f"\nSTOPPED EARLY: {exc}")
            print("Everything answered so far is in the run file below, and the "
                  "summary that follows covers only what actually ran.")
        finally:
            # Written before grading and before any summary, so a sweep that
            # dies half way through has still bought something.
            with open(run_file, "w", encoding="utf-8") as handle:
                json.dump(out, handle, indent=2, default=str)
            print(f"\nresponses written to {run_file}")

    return summarise(out, arms_wanted)


if __name__ == "__main__":
    sys.exit(main())
