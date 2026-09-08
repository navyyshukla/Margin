"""Eval checks for data/samples/coingecko_prices.json.

A record MAP, not a record list: {"bitcoin": {...}, "ethereum": {...}}. The
records are the values, and their identity is the key holding them.

Nothing in the compressor recognises this shape today — find_record_array looks
for a list of objects and finds none, so the payload is emitted as compact JSON
with a note. That is correct behaviour rather than a failure, but it is also the
single most promising unbuilt rule: eight objects with twelve identical keys
each is exactly what a table is for, and turning the map key into a column would
be fully reversible.

These checks exist so that when that rule is built, the thing it must not break
is already written down.

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
