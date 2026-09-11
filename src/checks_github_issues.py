"""Eval checks for data/samples/github_issues.json.

One module per sample payload: the checks are inseparable from the shape of
the data they interrogate, and mixing two payloads' questions in one file
makes it unclear which failure belongs to which.

Ground truth is computed from the raw payload at run time rather than
hardcoded, so a re-fetched sample keeps working. See data/eval_questions_github_issues.md
for the questions in prose, and src/eval_harness.py for how these are run.
"""

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

# The same questions in prose, for src/eval_model.py, which asks a model rather
# than calling the function. Keyed by the label prefix above.
#
# Each one states the ANSWER SHAPE ("a two-element array", "sorted
# alphabetically"), and that is not padding. The check functions return whatever
# Python was natural — a tuple here, a sorted list there — and the grader
# compares with same_json against that exact value. Without the shape spelled
# out, a model that understood the payload perfectly still fails on ordering, and
# the run measures our prompt rather than the compression.
ASK = {
    "Q1": "What is the title of issue number 37508? Give the exact title string.",
    "Q2": "What is the login of the user who opened issue number 37501?",
    "Q3": "What are the names of the labels on issue number 37510? Answer as an "
          "array of strings, in the order they appear.",
    "Q4": "How many of these entries are pull requests, and how many are plain "
          "issues? Answer as a two-element array: [pull_requests, plain_issues].",
    "Q5": "How many entries have exactly zero comments? Answer with a number.",
    "Q6": "What is the total number of comments across all entries? Answer with "
          "a number.",
    "Q7": "How many distinct user logins opened these entries? Answer with a number.",
    "Q8": "Which entries carry the label 'Type: Bug'? Answer as an array of their "
          "issue numbers, in the order they appear.",
    "Q9": "Which entries were opened by the user 'dependabot[bot]'? Answer as an "
          "array of their issue numbers, in the order they appear.",
    "Q10": "Which entries are plain issues rather than pull requests? Answer as an "
           "array of their issue numbers, in the order they appear.",
    "Q11": "Summarize issue 37534 in one sentence.",
    "Q12": "Which pull requests are dependency bumps, and what does each one touch?",
    # The adversarial one, and the only question whose right answer DIFFERS by
    # arm: true on the raw payload, false once strip_boilerplate has run. It is
    # the direct test of the failure docs/shapes.md now documents — a reader
    # inventing a link that is no longer there.
    "Q13": "Does any user object in this data carry an avatar_url field? Answer "
           "true or false.",
}
