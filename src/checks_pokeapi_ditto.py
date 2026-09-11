"""Eval checks for data/samples/pokeapi_ditto.json.

One deep object, not a list of records — a GET-by-id response, which is the most
common API shape of all and the one the other seven samples do not cover.

It is also the payload that decides whether "find the largest record array"
behaves sensibly when the arrays are incidental. Nothing here IS the record;
`game_indices`, `stats`, `abilities`, `types` and `held_items` are all lists of
objects hanging off a single subject. The compressor picks `game_indices` (46
rows, the largest by cells) and tabulates it, leaving everything else in #wrap.
That earns 6.2% — below the old 0.10 gate, above the re-derived 0.05 one.

So these questions deliberately ask about fields OUTSIDE the tabulated array.
That is where a wrapper bug would hide: the rows come back fine and the other
90% of the document quietly does not.

Ground truth is computed from the raw payload at run time, never hardcoded, so a
re-fetched sample keeps working. See src/checks_hn_stories.py for the shape.
"""


# --- PRESERVE checks --------------------------------------------------------

def p1_name_and_id(data):
    return data["name"], data["id"]


def p2_height_and_weight(data):
    return data["height"], data["weight"]


def p3_abilities(data):
    """Outside the tabulated array — lives in the skeleton."""
    return [a["ability"]["name"] for a in data["abilities"]]


def p4_base_stats(data):
    """Also outside it. Ditto's stats are all 48, which makes a dropped or
    duplicated entry invisible unless the names come back too."""
    return {s["stat"]["name"]: s["base_stat"] for s in data["stats"]}


def p5_types(data):
    return [t["type"]["name"] for t in data["types"]]


def p6_game_index_count(data):
    """The array that actually gets tabulated."""
    return len(data["game_indices"])


def p7_red_game_index(data):
    for entry in data["game_indices"]:
        if entry["version"]["name"] == "red":
            return entry["game_index"]
    return None


def p8_game_versions(data):
    return sorted(e["version"]["name"] for e in data["game_indices"])


def p9_is_default_and_order(data):
    """Scalars at the top level, easy to lose in a wrapper rewrite."""
    return data["is_default"], data["order"], data["base_experience"]


def p10_species_name(data):
    """A nested object in the skeleton, two levels from the root."""
    return data["species"]["name"]


PRESERVE_CHECKS = [
    ("P1  name and id", p1_name_and_id),
    ("P2  height and weight", p2_height_and_weight),
    ("P3  abilities (outside the table)", p3_abilities),
    ("P4  base stats (outside the table)", p4_base_stats),
    ("P5  types (outside the table)", p5_types),
    ("P6  game_indices count (the tabulated array)", p6_game_index_count),
    ("P7  game index for 'red'", p7_red_game_index),
    ("P8  all game versions", p8_game_versions),
    ("P9  is_default / order / base_experience", p9_is_default_and_order),
    ("P10 species name (nested in the skeleton)", p10_species_name),
]


def p_removed_urls(data):
    """PokeAPI hangs "url" off every nested reference, and uses the *_url
    convention nowhere. So uses_url_template_convention is False and those bare
    urls SURVIVE — which is the correct call: on this API a url is the pointer
    to the resource, exactly as HackerNews's url is the story.

    Asserting they survive rather than that they are gone, because the
    interesting risk on this payload is over-eager stripping, not under-eager.
    """
    return data["species"]["url"]


PRESERVE_CHECKS.append(("P11 nested resource url survives", p_removed_urls))

# strip_boilerplate removes nothing here: no *_url keys exist, so the bare "url"
# keys are content by the same rule that saves HackerNews's story links.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "P12 what kind of Pokemon is this and what are its abilities",
    "P13 which games does it appear in",
]

# Prose for src/eval_model.py; see the note in checks_github_issues.py for why
# each one states the answer shape.
#
# P8 has no entry on purpose: its answer is all 46 version names (603
# characters), which is transcription rather than comprehension. P13 asks the
# same thing of a reader in prose and IS asked, which is the useful half.
# Listed as not-asked, with that reason, on every run.
ASK = {
    "P1": "What are this Pokemon's name and id? Answer as a two-element array: "
          "[name, id].",
    "P2": "What are its height and weight? Answer as a two-element array: "
          "[height, weight].",
    "P3": "What are its ability names? Answer as an array of strings, in the order "
          "they appear.",
    "P4": "What are its base stats? Answer as an object mapping each stat name to "
          "its base_stat value.",
    "P5": "What are its type names? Answer as an array of strings, in the order "
          "they appear.",
    "P6": "How many game_indices entries are there? Answer with a number.",
    "P7": "What is the game_index for the version named 'red'? Answer with a number.",
    "P9": "What are is_default, order and base_experience? Answer as a "
          "three-element array in that order.",
    "P10": "What is the species name?",
    "P11": "What is the url of the species resource?",
    "P12": "What kind of Pokemon is this, and what are its abilities?",
    "P13": "Which games does it appear in?",
}
