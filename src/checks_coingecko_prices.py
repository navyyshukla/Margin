"""Eval checks for data/samples/coingecko_prices.json.

A record MAP, not a record list: {"bitcoin": {...}, "ethereum": {...}}. The
records are the values, and their identity is the key holding them.

These checks were written while the compressor still emitted this payload as
plain JSON, so that the rule which turned it into a table had something to be
measured against rather than something to be justified by. That rule now exists
(`table._is_record_map`): the map key becomes a column, `#keyed` names it, and
coingecko went 0% -> 29.6% (30.6% before the #keyed legend clause grew on
2026-09-12; docs/thresholds.md).

What they guard now is the thing that rule can most easily get wrong — the map
keys ARE the records' identity, so losing or reordering them leaves eight
anonymous rows that still round-trip perfectly.

Ground truth is computed from the raw payload at run time, never hardcoded, so a
re-fetched sample keeps working. See src/checks_hn_stories.py for the shape.
"""


# --- PRESERVE checks --------------------------------------------------------

def c1_coin_count(data):
    return len(data)


def c2_coin_ids(data):
    """The map keys ARE the records' identity — lose them and the rows are
    anonymous. Any future record-map rule has to put them back exactly."""
    return sorted(data)


def c3_bitcoin_usd(data):
    return data["bitcoin"]["usd"]


def c4_every_coin_has_the_same_fields(data):
    return len({tuple(sorted(fields)) for fields in data.values()}) == 1


def c5_all_usd_prices(data):
    return {coin: fields["usd"] for coin, fields in data.items()}


def c6_market_caps_are_floats(data):
    """Floats that print like large decimals — the type must not drift to int."""
    return {coin: type(fields["usd_market_cap"]).__name__ for coin, fields in data.items()}


def c7_negative_changes_survive(data):
    """24h change is often negative; a sign lost in encoding inverts the answer."""
    return sorted(coin for coin, f in data.items() if f["usd_24h_change"] < 0)


def c8_full_ethereum_record(data):
    return data["ethereum"]


PRESERVE_CHECKS = [
    ("C1  coin count", c1_coin_count),
    ("C2  coin ids (the map keys)", c2_coin_ids),
    ("C3  bitcoin usd price", c3_bitcoin_usd),
    ("C4  every coin has the same fields", c4_every_coin_has_the_same_fields),
    ("C5  all usd prices", c5_all_usd_prices),
    ("C6  market caps stay floats", c6_market_caps_are_floats),
    ("C7  coins down over 24h (sign preserved)", c7_negative_changes_survive),
    ("C8  full ethereum record", c8_full_ethereum_record),
]

# No *_url keys anywhere, so strip_boilerplate removes nothing.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "C9  which coin has the largest market cap",
    "C10 which coins are up over the last 24 hours",
]

# Prose for src/eval_model.py; see the note in checks_github_issues.py for why
# each one states the answer shape.
#
# C6 has no entry on purpose. It asks what Python type each market cap decoded
# to, which is a question about the decoder rather than about the data — there is
# no way to phrase it to a reader of a document. src/eval_model.py lists it as
# not-asked, with that reason, on every run.
ASK = {
    "C1": "How many coins are in this data? Answer with a number.",
    "C2": "What are the coin ids? Answer as an array of strings sorted "
          "alphabetically.",
    "C3": "What is bitcoin's usd price? Answer with a number.",
    "C4": "Does every coin carry exactly the same set of fields? Answer true or "
          "false.",
    "C5": "What is each coin's usd price? Answer as an object mapping coin id to "
          "its usd price.",
    "C7": "Which coins have a negative usd_24h_change? Answer as an array of coin "
          "ids sorted alphabetically.",
    "C8": "Give the complete record for ethereum, as an object with every field it "
          "has.",
    "C9": "Which coin has the largest market cap?",
    "C10": "Which coins are up over the last 24 hours?",
}
