"""Automated eval harness — makes data/eval_questions.md checkable by a script
instead of by hand.

Why this exists: the ground-truth answers in data/eval_questions.md were verified
by running one-off snippets in the terminal. That doesn't scale — once several
compression rules stack up, re-checking every question by hand is exactly how a
bad rule slips through unnoticed.

Ground truth is computed fresh from the raw payload on every run, not hardcoded,
because Margin's actual claim is a comparison — "the compressed payload answers
the same as the raw one" — not "the payload contains these specific values."
That also means a newly fetched sample file works with no edits here.

Two kinds of check:
  - PRESERVE: the answer must be identical raw vs compressed (questions 1-10).
  - REMOVED:  the field must be gone from the compressed payload (question 13,
              the adversarial one) — here a difference is the pass condition.

Questions 11-12 need real reading comprehension and stay manual; string-matching
them would give false confidence.

Usage: python src/eval_harness.py data/samples/github_issues.json
"""

import json
import sys

from compress import strip_boilerplate
from detect import detect_content_type


def issue_by_number(data, number):
    """The one issue with this number, or None."""
    for issue in data:
        if issue["number"] == number:
            return issue
    return None


# --- PRESERVE checks: each returns the answer to one question ---------------
# Each takes the payload and returns a plain value. The harness runs the same
# function on the raw and the compressed payload and compares the two results.

def q1_title_of_37508(data):
    return issue_by_number(data, 37508)["title"]


def q2_author_of_37501(data):
    return issue_by_number(data, 37501)["user"]["login"]


def q3_labels_of_37510(data):
    return [label["name"] for label in issue_by_number(data, 37510)["labels"]]


def q4_pr_vs_plain_counts(data):
    prs = [i for i in data if "pull_request" in i]
    return len(prs), len(data) - len(prs)


def q5_zero_comment_count(data):
    return len([i for i in data if i["comments"] == 0])


def q6_total_comments(data):
    return sum(i["comments"] for i in data)


def q7_distinct_users(data):
    return len({i["user"]["login"] for i in data})


def q8_type_bug_issues(data):
    return [i["number"] for i in data
            if "Type: Bug" in [label["name"] for label in i["labels"]]]


def q9_dependabot_issues(data):
    return [i["number"] for i in data if i["user"]["login"] == "dependabot[bot]"]


def q10_plain_issue_numbers(data):
    return [i["number"] for i in data if "pull_request" not in i]


PRESERVE_CHECKS = [
    ("Q1  title of #37508", q1_title_of_37508),
    ("Q2  author of #37501", q2_author_of_37501),
    ("Q3  labels on #37510", q3_labels_of_37510),
    ("Q4  PR vs plain counts", q4_pr_vs_plain_counts),
    ("Q5  issues with zero comments", q5_zero_comment_count),
    ("Q6  total comment count", q6_total_comments),
    ("Q7  distinct users", q7_distinct_users),
    ("Q8  issues labeled Type: Bug", q8_type_bug_issues),
    ("Q9  issues by dependabot", q9_dependabot_issues),
    ("Q10 plain (non-PR) issue numbers", q10_plain_issue_numbers),
]


# --- REMOVED checks: the field must be absent after compression -------------

def q13_avatar_url_present(data):
    """True if any user object still carries an avatar_url."""
    return any("avatar_url" in i["user"] for i in data)


REMOVED_CHECKS = [
    ("Q13 avatar_url gone (adversarial)", q13_avatar_url_present),
]

MANUAL_QUESTIONS = [
    "Q11 summarize #37534 in one sentence",
    "Q12 which PRs are dependency bumps, and what do they touch",
]


def safe_answer(check, data):
    """Run a check, turning a crash into a reportable value.

    An over-aggressive rule deletes a field the check needs, so the check
    raises (KeyError/TypeError) instead of returning. That IS a failure, but
    it has to be reported as one — a traceback would kill the run and hide
    every check after it.
    """
    try:
        return check(data), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def run(data):
    """Compare answers on the raw payload vs the compressed one.

    Returns the number of failures.
    """
    compressed = strip_boilerplate(data)
    failures = 0

    print("PRESERVE — answer must be identical after compression")
    for label, check in PRESERVE_CHECKS:
        expected, raw_error = safe_answer(check, data)
        actual, compressed_error = safe_answer(check, compressed)

        if raw_error:
            # The check is broken against the RAW payload — that's a bug in the
            # question, not evidence about the compression.
            failures += 1
            print(f"  ERROR {label}")
            print(f"        check failed on the raw payload: {raw_error}")
            continue

        if compressed_error:
            failures += 1
            print(f"  FAIL  {label}")
            print(f"        raw:        {expected!r}")
            print(f"        compressed: could not answer — {compressed_error}")
            continue

        if expected == actual:
            print(f"  PASS  {label}")
        else:
            failures += 1
            print(f"  FAIL  {label}")
            print(f"        raw:        {expected!r}")
            print(f"        compressed: {actual!r}")

    print("\nREMOVED — field must be gone after compression")
    for label, check in REMOVED_CHECKS:
        in_raw, raw_error = safe_answer(check, data)
        in_compressed, compressed_error = safe_answer(check, compressed)

        if raw_error or compressed_error:
            failures += 1
            print(f"  ERROR {label}")
            print(f"        {raw_error or compressed_error}")
        elif in_raw and not in_compressed:
            print(f"  PASS  {label}")
        else:
            failures += 1
            print(f"  FAIL  {label}")
            print(f"        present in raw:        {in_raw}")
            print(f"        present in compressed: {in_compressed}")

    print("\nMANUAL — needs a human or an LLM, not checked here")
    for label in MANUAL_QUESTIONS:
        print(f"  ----  {label}")

    return failures


def main():
    if len(sys.argv) != 2:
        print("usage: python src/eval_harness.py <path-to-json-file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        raw_text = f.read()

    content_type, data = detect_content_type(raw_text)
    if content_type != "json":
        print(f"{sys.argv[1]} is {content_type}, not json — nothing to evaluate",
              file=sys.stderr)
        sys.exit(1)

    failures = run(data)

    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
