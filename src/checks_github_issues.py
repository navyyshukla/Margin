"""Eval checks for data/samples/github_issues.json.

One module per sample payload: the checks are inseparable from the shape of
the data they interrogate, and mixing two payloads' questions in one file
makes it unclear which failure belongs to which.

Ground truth is computed from the raw payload at run time rather than
hardcoded, so a re-fetched sample keeps working. See data/eval_questions.md
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
