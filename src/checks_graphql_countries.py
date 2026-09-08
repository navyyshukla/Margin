"""Eval checks for data/samples/graphql_countries.json.

A GraphQL response: everything is wrapped in {"data": {...}}, which is the shape
that compressed by exactly 0% until 2026-09-08 because record discovery only
looked one level deep. So the first thing these questions assert is that the
records are found at all.

It also carries the two nested shapes GitHub and HackerNews between them never
produced together: a single-key object per row (continent) and an array of
objects per row (languages), 250 rows of both.

Ground truth is computed from the raw payload at run time, never hardcoded, so a
re-fetched sample keeps working. See src/checks_hn_stories.py for the shape.
"""


def countries(data):
    """The records, two levels down — the case that used to be invisible."""
    return data["data"]["countries"]


def country_by_code(data, code):
    for country in countries(data):
        if country["code"] == code:
            return country
    return None


# --- PRESERVE checks --------------------------------------------------------

def g1_country_count(data):
    return len(countries(data))


def g2_name_of_japan(data):
    return country_by_code(data, "JP")["name"]


def g3_capital_of_france(data):
    return country_by_code(data, "FR")["capital"]


def g4_continent_of_brazil(data):
    """A nested single-key object, flattened to continent.name and back."""
    return country_by_code(data, "BR")["continent"]


def g5_languages_of_switzerland(data):
    """An array of objects — stays a JSON cell, and must come back intact."""
    return country_by_code(data, "CH")["languages"]


def g6_countries_using_eur(data):
    return sorted(c["code"] for c in countries(data) if c["currency"] == "EUR")


def g7_distinct_continents(data):
    return sorted({c["continent"]["name"] for c in countries(data)})


def g8_countries_with_no_capital(data):
    """Genuine nulls, not absent keys — the distinction \\N exists to preserve."""
    return sorted(c["code"] for c in countries(data) if c["capital"] is None)


def g9_emoji_flag_of_india(data):
    """Multi-codepoint emoji through csv — a real unicode round-trip."""
    return country_by_code(data, "IN")["emoji"]


def g10_multilingual_countries(data):
    return sorted(c["code"] for c in countries(data) if len(c["languages"]) > 2)


def g11_wrapper_survives(data):
    """The GraphQL envelope itself. Nothing else here would notice if the
    "data" key were dropped and the rows re-rooted at the top level."""
    return sorted(data) == ["data"] and sorted(data["data"]) == ["countries"]


PRESERVE_CHECKS = [
    ("G1  country count", g1_country_count),
    ("G2  name of JP", g2_name_of_japan),
    ("G3  capital of FR", g3_capital_of_france),
    ("G4  continent of BR (nested object)", g4_continent_of_brazil),
    ("G5  languages of CH (array of objects)", g5_languages_of_switzerland),
    ("G6  countries using EUR", g6_countries_using_eur),
    ("G7  distinct continents", g7_distinct_continents),
    ("G8  countries with a null capital", g8_countries_with_no_capital),
    ("G9  emoji flag of IN (unicode)", g9_emoji_flag_of_india),
    ("G10 countries with >2 languages", g10_multilingual_countries),
    ("G11 the {\"data\": ...} envelope survives", g11_wrapper_survives),
]

# No *_url keys anywhere, so strip_boilerplate removes nothing.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "G12 which countries in South America use Spanish",
    "G13 name three countries whose capital you can find here and say what continent each is on",
]
