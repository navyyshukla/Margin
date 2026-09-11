"""Eval checks for data/samples/hn_stories.json (HackerNews via Algolia).

The second payload exists to answer one question: do the compression rules
generalize, or were they quietly shaped around GitHub? So these questions
deliberately lean on the ways this API differs — records nested under "hits"
rather than at the top level, a "url" that is the story itself rather than a
link template, arrays of plain scalars, and a story with no url at all.

Ground truth is computed from the raw payload at run time, never hardcoded, so
a re-fetched sample keeps working. See src/checks_github.py for the shape.
"""


def stories(data):
    """The records, which on this API live under a key rather than at the root."""
    return data["hits"]


def story_by_id(data, object_id):
    for story in stories(data):
        if story["objectID"] == object_id:
            return story
    return None


# --- PRESERVE checks --------------------------------------------------------

def h1_title_of_top_story(data):
    return max(stories(data), key=lambda s: s["points"])["title"]


def h2_author_of_hawking_story(data):
    return story_by_id(data, "16582136")["author"]


def h3_url_of_hawking_story(data):
    """The url field specifically — this is the one strip_boilerplate must NOT eat.

    On GitHub a bare "url" is a link template and gets dropped. Here it is the
    story, and dropping it would delete the answer rather than the noise.
    """
    return story_by_id(data, "16582136")["url"]


def h4_story_count(data):
    return len(stories(data))


def h5_total_points(data):
    return sum(s["points"] for s in stories(data))


def h6_total_comments(data):
    return sum(s["num_comments"] for s in stories(data))


def h7_distinct_authors(data):
    return len({s["author"] for s in stories(data)})


def h8_stories_without_url(data):
    """Which stories have no url — a genuine null, not an absent key."""
    return [s["objectID"] for s in stories(data) if not s.get("url")]


def h9_most_discussed_title(data):
    return max(stories(data), key=lambda s: s["num_comments"])["title"]


def h10_tags_of_hawking_story(data):
    """An array of plain scalars, not objects — a shape GitHub never produced."""
    return story_by_id(data, "16582136")["_tags"]


def h11_wrapper_metadata_survives(data):
    """The wrapper's own fields, which sit beside the records rather than in them.

    Nothing else here would notice if the table transform dropped them.
    """
    return data["nbHits"], data["hitsPerPage"], data["page"]


def h14_first_comment_id_of_hawking_story(data):
    """The one graded question here whose answer lives in the store.

    `children` is 156 comment ids, delta-encoded as `dints` and — under --store —
    moved out of the document entirely: the cell renders as \\@nnnn[316t], so a
    reader has to fetch it before it can answer. Every other H check reads a
    column that stays put, which is why cold read #5 never called `fetch` on this
    payload at all (docs/cold-reads/2026-09-11.md).

    The FIRST id, not the count. A dints cell states its first value absolutely
    and the rest as differences, so this asks the reader to apply exactly one
    clause of the legend to exactly one token. Counting 156 of them is the thing
    all five cold reads have got wrong at least once (docs/cold-reads.md), and a
    question that failed on arithmetic would say nothing about retrieval.
    """
    return story_by_id(data, "16582136")["children"][0]


PRESERVE_CHECKS = [
    ("H1  title of the top-scoring story", h1_title_of_top_story),
    ("H2  author of story 16582136", h2_author_of_hawking_story),
    ("H3  url of story 16582136 (must survive)", h3_url_of_hawking_story),
    ("H4  story count", h4_story_count),
    ("H5  total points", h5_total_points),
    ("H6  total comments", h6_total_comments),
    ("H7  distinct authors", h7_distinct_authors),
    ("H8  stories with no url", h8_stories_without_url),
    ("H9  most-discussed story title", h9_most_discussed_title),
    ("H10 _tags of story 16582136", h10_tags_of_hawking_story),
    ("H11 wrapper metadata survives", h11_wrapper_metadata_survives),
    ("H14 first comment id of story 16582136", h14_first_comment_id_of_hawking_story),
]

# Nothing to assert here yet: strip_boilerplate finds no *_url keys anywhere in
# this payload, so it removes nothing at all — which is the correct behaviour,
# not an oversight. A REMOVED check would have to invent something to delete.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "H12 summarize what the top 3 stories are about",
    "H13 which stories are about AI companies, and what happened in each",
]

# Prose for src/eval_model.py; see the note in checks_github_issues.py for why
# each one states the answer shape.
ASK = {
    "H1": "What is the title of the story with the highest points? Give the exact "
          "title string.",
    "H2": "Who is the author of the story whose objectID is 16582136?",
    "H3": "What is the url of the story whose objectID is 16582136?",
    "H4": "How many stories are in this data? Answer with a number.",
    "H5": "What is the sum of points across all stories? Answer with a number.",
    "H6": "What is the sum of num_comments across all stories? Answer with a number.",
    "H7": "How many distinct authors are there? Answer with a number.",
    "H8": "Which stories have no url (null, absent or empty)? Answer as an array "
          "of their objectID strings, in the order they appear.",
    "H9": "What is the title of the story with the most comments? Give the exact "
          "title string.",
    "H10": "What are the _tags of the story whose objectID is 16582136? Answer as "
           "an array of strings, in the order they appear.",
    "H11": "What are the values of nbHits, hitsPerPage and page? Answer as a "
           "three-element array in that order.",
    # Says "first listed", not "smallest": the ids are not in ascending order,
    # and asking for a minimum would send the reader through all 156 of them.
    "H14": "What is the first comment id listed in the children of the story whose "
           "objectID is 16582136? Answer with a number.",
    "H12": "Summarize what the top 3 stories by points are about.",
    "H13": "Which stories are about AI companies, and what happened in each?",
}
