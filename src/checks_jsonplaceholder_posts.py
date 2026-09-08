"""Eval checks for data/samples/jsonplaceholder_posts.json.

100 flat records, four keys each, no nesting anywhere. The opposite extreme from
GitHub: nothing to flatten, nothing constant, no arrays. What it tests is
whether the table's fixed cost (one header line) still pays when the rows are
small — the header names four columns and is then amortised over a hundred rows,
which should be the format at its most favourable per row.

Ground truth is computed from the raw payload at run time, never hardcoded, so a
re-fetched sample keeps working. See src/checks_hn_stories.py for the shape.
"""


def post_by_id(data, post_id):
    for post in data:
        if post["id"] == post_id:
            return post
    return None


# --- PRESERVE checks --------------------------------------------------------

def j1_post_count(data):
    return len(data)


def j2_title_of_post_50(data):
    return post_by_id(data, 50)["title"]


def j3_body_of_post_1(data):
    """A multi-line body — the CSV cell has to survive embedded newlines."""
    return post_by_id(data, 1)["body"]


def j4_distinct_user_ids(data):
    return sorted({post["userId"] for post in data})


def j5_posts_per_user(data):
    counts = {}
    for post in data:
        counts[post["userId"]] = counts.get(post["userId"], 0) + 1
    return counts


def j6_ids_are_contiguous(data):
    """1..100 with no gaps — catches a row silently lost in the round-trip."""
    return sorted(post["id"] for post in data) == list(range(1, len(data) + 1))


def j7_longest_title(data):
    return max(data, key=lambda post: len(post["title"]))["title"]


def j8_every_post_has_all_four_keys(data):
    """No row shape variation at all, which is itself the thing worth asserting:
    a union schema must not invent a column, and MISSING must never appear."""
    return sorted({tuple(sorted(post)) for post in data})


PRESERVE_CHECKS = [
    ("J1  post count", j1_post_count),
    ("J2  title of post 50", j2_title_of_post_50),
    ("J3  body of post 1 (multi-line)", j3_body_of_post_1),
    ("J4  distinct userIds", j4_distinct_user_ids),
    ("J5  posts per user", j5_posts_per_user),
    ("J6  ids are contiguous 1..N", j6_ids_are_contiguous),
    ("J7  longest title", j7_longest_title),
    ("J8  every row has the same four keys", j8_every_post_has_all_four_keys),
]

# No *_url keys anywhere in this payload, so strip_boilerplate removes nothing.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "J9  what is post 42 about",
    "J10 which user writes the shortest posts on average",
]
