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
]

# Nothing to assert here yet: strip_boilerplate finds no *_url keys anywhere in
# this payload, so it removes nothing at all — which is the correct behaviour,
# not an oversight. A REMOVED check would have to invent something to delete.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "H12 summarize what the top 3 stories are about",
    "H13 which stories are about AI companies, and what happened in each",
]
