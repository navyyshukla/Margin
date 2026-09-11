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

  GRADED (75)  the existing PRESERVE_CHECKS and REMOVED_CHECKS. Ground truth is
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
import random
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

# What a run file records when the judging was done offline, by subagents.
#
# Named rather than left as the pinned id, because a run file must never imply a
# model that did not run it — the same reason the offline reader path writes
# "(offline reader — see the task files)" instead of ANSWER_MODEL.
#
# **This judge shares a model family with the answerer**, which a paid judge from
# another vendor would not. It is the weaker instrument and the docs say so in
# those words; JUDGED_ALLOWED_GAP bounds what may be claimed from it rather than
# removing the bias. It is also asked only whether two answers SAY THE SAME
# THING, never which is correct, which narrows the room a shared prior has to
# work in — narrows, not closes.
OFFLINE_JUDGE = "(offline judge — subagents, same model family as the answerer)"

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


# --- the offline judge ----------------------------------------------------

# The comparison a judge is asked to make, with the arms stripped out of it.
#
# judge()'s own prompt says "The first was produced from the original data; the
# second from a compressed form of it" — fine when the reader is a paid API model
# that will never see anything else, and a leak when it is a subagent that can
# guess which side it is expected to prefer. So the sides are A and B, which one
# is the reference is randomised per task, and the mapping lives only in
# _judge_truth.json. Same isolation emit() already applies to readers: not even
# which arm it is.
JUDGE_TASK = """You are comparing two answers to the same question.

Both answers were written about the same underlying data. Decide whether they
say substantially the SAME thing. Differences in wording, length, ordering or
level of detail do not matter. A fact present in one and missing from the other,
a fact they contradict each other on, or a fact one of them invented, does.

Neither answer is authoritative. You are not judging which is better or which is
correct — only whether they say the same thing.

Reply with a single JSON object and nothing else:

    {{"same": true, "why": "one sentence"}}

QUESTION: {question}

ANSWER A:
{first}

ANSWER B:
{second}
"""


def _judge_pairs(out):
    """Every (payload, label, arm) in a run file that a judge could rule on.

    Skips arms with no answer, arms that ARE raw, and by_construction twins —
    a twin inherits its verdict the way run_payload does rather than being
    judged twice for the same bytes.
    """
    for payload in out["payloads"]:
        for row in payload.get("judged", []):
            raw = row["arms"].get("raw")
            if not raw or not str(raw.get("answer") or "").strip():
                continue
            for arm, rec in row["arms"].items():
                if arm == "raw" or rec.get("by_construction"):
                    continue
                if not str(rec.get("answer") or "").strip():
                    continue
                yield payload["payload"], row["label"], arm, raw, rec


def _control_pairs(out, rng):
    """Pairs whose verdict is known before anybody judges them.

    The first judged run came back 18 "same" out of 18, and a judge that has
    never once said "different" is indistinguishable from a judge that cannot.
    That is Rule 14 — *a detector nobody has shown a positive to is not a
    detector* — pointed at the judge instead of at the store, and the fix is the
    same one `detects_a_broken_store()` uses: feed it hand-built positives.

    Two kinds, both built only from raw answers so no arm is involved:

      SAME       one answer against itself. A judge that fails this is broken
                 in the direction that would silently pass every real pair.
      DIFFERENT  two answers to DIFFERENT questions about the same payload.
                 They are genuinely not the same thing, and a judge that calls
                 them same is the failure mode that makes 16/16 meaningless.
    """
    controls = []
    for payload in out["payloads"]:
        answers = [(row["label"], row["arms"]["raw"].get("answer"))
                   for row in payload.get("judged", [])
                   if row["arms"].get("raw")
                   and str(row["arms"]["raw"].get("answer") or "").strip()]
        if not answers:
            continue
        label, text = answers[0]
        controls.append({"kind": "control", "expect": True, "label": label,
                         "first": text, "second": text,
                         "payload": payload["payload"]})
        if len(answers) > 1:
            other_label, other = answers[1]
            # The question shown is the FIRST one, so the pair is a real
            # mismatch rather than two answers to two questions nobody asked
            # together — the shape a compressed arm would produce if it had
            # answered the wrong thing entirely.
            controls.append({"kind": "control", "expect": False, "label": label,
                             "first": text, "second": other,
                             "payload": payload["payload"],
                             "note": f"second is the answer to {other_label}"})
    rng.shuffle(controls)
    return controls


def emit_judge(run_file, out_dir):
    """Write one blinded comparison task per judged answer, plus controls.

    The free path for the half that has never been measured. `--emit` has been
    collecting these free-text answers since it was written and `--grade` stored
    them and ignored them; this turns them into a verdict without buying a model.
    """
    with open(run_file, encoding="utf-8") as handle:
        out = json.load(handle)

    os.makedirs(out_dir, exist_ok=True)
    answers_dir = os.path.join(out_dir, "judge_answers")
    if os.path.isdir(answers_dir) and os.listdir(answers_dir):
        print(f"refusing to emit: {answers_dir} already holds verdicts.",
              file=sys.stderr)
        print("Move or delete them first — they belong to a previous run.",
              file=sys.stderr)
        return 2
    os.makedirs(answers_dir, exist_ok=True)

    # Seeded so a re-emit of the same run file produces the same blinding. A
    # judge task that silently changes sides between emits would make two runs
    # of "the same" comparison not comparable.
    rng = random.Random(run_file)

    # Real pairs and controls, shuffled together and named by number.
    #
    # The names used to be `<payload>__<qid>__<arm>`, which told a judge reading
    # its own filename which arm it was ruling on — the leak this file removes
    # from the prompt, left in the one place nobody looked. Opaque ids also make
    # a control indistinguishable from a real pair, which is the whole point of
    # having controls.
    pending = [{"kind": "real", "payload": payload, "label": label, "arm": arm,
                "raw": raw, "rec": rec}
               for payload, label, arm, raw, rec in _judge_pairs(out)]
    pending += _control_pairs(out, rng)
    rng.shuffle(pending)

    tasks = []
    for number, item in enumerate(pending, start=1):
        name = f"judge_{number:03d}"
        reference_first = rng.random() < 0.5
        if item["kind"] == "real":
            sides = (item["raw"].get("answer"), item["rec"].get("answer"))
        else:
            sides = (item["first"], item["second"])
        first, second = sides if reference_first else (sides[1], sides[0])
        with open(os.path.join(out_dir, name + ".md"), "w",
                  encoding="utf-8") as handle:
            handle.write(JUDGE_TASK.format(question=item["label"],
                                           first=first, second=second))
        entry = {"task": name, "kind": item["kind"], "payload": item["payload"],
                 "label": item["label"],
                 "reference_is": "A" if reference_first else "B"}
        if item["kind"] == "real":
            entry["arm"] = item["arm"]
        else:
            entry["expect"] = item["expect"]
            if item.get("note"):
                entry["note"] = item["note"]
        tasks.append(entry)

    key_path = os.path.join(out_dir, "_judge_truth.json")
    with open(key_path, "w", encoding="utf-8") as handle:
        json.dump({"run_file": os.path.abspath(run_file), "tasks": tasks},
                  handle, indent=2)

    real = [t for t in tasks if t["kind"] == "real"]
    controls = [t for t in tasks if t["kind"] == "control"]
    print(f"{len(tasks)} judging task(s) in {out_dir}")
    by_arm = collections.Counter(task["arm"] for task in real)
    for arm, count in sorted(by_arm.items()):
        print(f"  {arm:<12} {count}")
    print(f"  {'controls':<12} {len(controls)} "
          f"({sum(1 for t in controls if t['expect'])} must read same, "
          f"{sum(1 for t in controls if not t['expect'])} must read different)")
    if not real:
        print("  nothing to judge — the run file has no judged answers. "
              "Was it graded from tasks emitted with --no-judge?")
    print(f"\nthe key, including which tasks are controls: {key_path} — "
          "never show it to a judge")
    print(f"verdicts go in {answers_dir}/<task>.json as "
          '{"same": true|false, "why": "..."}')
    return 0


def ingest_judgements(task_dir, arms_wanted):
    """Fold offline verdicts into the run file they came from, then re-summarise.

    Writes `same_as_raw` onto the judged records, which is the entire contract
    summarise() needs — the run-file format is unchanged.
    """
    with open(os.path.join(task_dir, "_judge_truth.json"),
              encoding="utf-8") as handle:
        key = json.load(handle)
    with open(key["run_file"], encoding="utf-8") as handle:
        out = json.load(handle)

    def verdict_for(task_name):
        path = os.path.join(task_dir, "judge_answers", task_name + ".json")
        if not os.path.exists(path):
            return None, "no verdict file"
        with open(path, encoding="utf-8") as handle:
            given = json.load(handle)
        if not isinstance(given, dict) or "same" not in given:
            return None, "no verdict in the file"
        return bool(given["same"]), str(given.get("why", ""))[:500]

    # The controls first, and nothing is written if they fail.
    #
    # This is RAW_MIN_GRADED_FRACTION's move applied to the judge: a comparison
    # against a reference that has itself collapsed is not a comparison. The
    # first judged run returned "same" 18 times out of 18 — which is either a
    # real result or a judge that cannot say "different", and there was no way
    # to tell. Now there is, and a judge that fails its own controls produces no
    # judged number at all rather than a flattering one.
    controls = [t for t in key["tasks"] if t["kind"] == "control"]
    control_failures = []
    for task in controls:
        same, why = verdict_for(task["task"])
        if same is None:
            control_failures.append(f"{task['task']}: {why}")
        elif same != task["expect"]:
            control_failures.append(
                f"{task['task']}: expected "
                f"{'same' if task['expect'] else 'different'}, judge said "
                f"{'same' if same else 'different'} ({why[:120]})")

    if controls and control_failures:
        print(f"\nJUDGE REJECTED — {len(control_failures)} of {len(controls)} "
              f"control(s) failed:")
        for line in control_failures:
            print(f"  {line}")
        print("\nNo judged verdict was written. A judge that cannot rule "
              "correctly on pairs whose answer was known in advance cannot be "
              "trusted on the pairs whose answer is the point (Rule 14).")
        return 2
    if not controls:
        print("\nWARNING: this key has no controls, so nothing has shown this "
              "judge can say 'different'. Re-emit to get them.")

    index = {(task["payload"], task["label"], task["arm"]): task
             for task in key["tasks"] if task["kind"] == "real"}

    verdicts = 0
    missing = []
    for payload in out["payloads"]:
        for row in payload.get("judged", []):
            raw = row["arms"].get("raw")
            if raw is not None and str(raw.get("answer") or "").strip():
                # Raw is trivially the same as itself, and saying so is load
                # bearing: summarise() counts raw's judged_ok as the reference
                # every gap is measured from, so leaving it unset makes every
                # gap negative and every arm PASS vacuously.
                raw["same_as_raw"] = True
            for arm, rec in row["arms"].items():
                if arm == "raw":
                    continue
                task = index.get((payload["payload"], row["label"], arm))
                if task is None:
                    continue
                same, why = verdict_for(task["task"])
                if same is None:
                    missing.append(f"{task['task']} ({why})")
                    continue
                rec["same_as_raw"] = same
                rec["judge_note"] = why
                verdicts += 1

    # Twins inherit, exactly as run_payload does: identical bytes cannot produce
    # a different answer, so judging them twice would be measuring the judge.
    for payload in out["payloads"]:
        for row in payload.get("judged", []):
            for arm, rec in row["arms"].items():
                twin = rec.get("by_construction")
                if twin and "same_as_raw" not in rec:
                    source = row["arms"].get(twin, {})
                    if "same_as_raw" in source:
                        rec["same_as_raw"] = source["same_as_raw"]
                        rec["judge_note"] = source.get("judge_note", "")

    out["judge_model"] = OFFLINE_JUDGE
    out["judge_controls"] = {"n": len(controls), "failed": 0}
    with open(key["run_file"], "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, default=str)

    if controls:
        print(f"\njudge controls: {len(controls)}/{len(controls)} correct "
              f"({sum(1 for t in controls if not t['expect'])} of them pairs it "
              f"had to call DIFFERENT)")
    print(f"{verdicts} verdict(s) folded into {key['run_file']}")
    if missing:
        print(f"no verdict yet for {len(missing)} task(s): "
              f"{', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}")

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

        # 4. The same shape, one half over. Until the offline judge existed the
        #    judged tally was always 0/0 and printed "not measured", so nothing
        #    needed guarding. Now a judge pass that covered three payloads out of
        #    eight would print a clean JUDGED gap off a third of the data —
        #    which is reason 2 again, wearing the other half's clothes.
        judged_n = tally[arm]["judged_n"]
        if judged_n and judged_n != raw["judged_n"]:
            reasons.append(
                f"the {arm} arm was judged on {judged_n} questions against raw's "
                f"{raw['judged_n']} — different denominators, so the judged gap "
                f"is not a difference in quality either")
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


def _fake_run(arms, judged=None):
    """A run file shaped enough for summarise(), built from {arm: (ok, n)}.

    `judged` takes the same shape and fills the judged half, which used to be
    hardcoded empty — so every judged failure mode below was unreachable from
    here while the judged tally was always 0/0 in practice too. An offline judge
    makes those reachable, so they became cases (Rule 13).
    """
    rows = []
    for i in range(max(n for _, n in arms.values())):
        row = {"label": f"Q{i + 1}", "removed": False, "arms": {}}
        for arm, (ok, n) in arms.items():
            if i < n:
                row["arms"][arm] = {"correct": i < ok, "fetch_calls": []}
        rows.append(row)

    judged_rows = []
    if judged:
        for i in range(max(n for _, n in judged.values())):
            row = {"label": f"J{i + 1}", "arms": {}}
            for arm, (ok, n) in judged.items():
                if i < n:
                    row["arms"][arm] = {"same_as_raw": i < ok, "fetch_calls": []}
            judged_rows.append(row)

    return {"payloads": [{"payload": "fake.json", "graded": rows,
                          "judged": judged_rows, "stored_has_handles": False}]}


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

    def verdict(arms, judged=None):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = summarise(_fake_run(arms, judged), tuple(arms))
        return code, buffer.getvalue()

    failures = []

    def expect(name, arms, code, must_say=None, must_not_say=None, judged=None):
        got, text = verdict(arms, judged)
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

    # The judged half, now that an offline judge can actually fill it. Each of
    # these is the graded half's own history repeated one column over, which is
    # the argument for writing them before the first judged run rather than
    # after the first judged surprise.

    # It reports at all, and a level judged half passes.
    expect("judged level", {"raw": (10, 10), "compressed": (10, 10)}, 0,
           judged={"raw": (4, 4), "compressed": (4, 4)},
           must_say="JUDGED gap")

    # JUDGED_ALLOWED_GAP is 1, so one behind is noise and two is a regression.
    expect("judged two behind", {"raw": (10, 10), "compressed": (10, 10)}, 1,
           judged={"raw": (4, 4), "compressed": (2, 4)},
           must_say="THRESHOLDS NOT MET")

    # The one that made this worth writing: if raw never records same_as_raw,
    # its judged_ok is 0, every gap goes negative, and every arm PASSes on a
    # comparison against nothing. Rule 1 in the judged column.
    expect("raw judged on nothing", {"raw": (10, 10), "compressed": (10, 10)}, 2,
           judged={"raw": (0, 0), "compressed": (4, 4)},
           must_say="denominators")

    print(f"verdict self-test: {9 - len(failures)}/9 cases hold")
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
    parser.add_argument("--emit-judge", metavar="RUNFILE",
                        help="write blinded comparison tasks for the judged "
                             "questions in RUNFILE; --out names where")
    parser.add_argument("--out", metavar="DIR",
                        help="where --emit-judge writes (default beside the run)")
    parser.add_argument("--judge-in", metavar="DIR",
                        help="fold the verdicts under DIR/judge_answers/ back "
                             "into the run file they came from, and re-summarise")
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

    # The other half of the same idea, and the reason the judged column stopped
    # reading "not measured" without anything being bought: --emit has been
    # collecting the comprehension answers all along and --grade stored them and
    # scored nothing. These two turn them into a verdict.
    if args.emit_judge:
        out_dir = args.out or os.path.join(repo, "data", "judge_tasks")
        return emit_judge(args.emit_judge, out_dir)
    if args.judge_in:
        return ingest_judgements(args.judge_in, arms_wanted)

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
