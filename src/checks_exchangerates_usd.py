"""Eval checks for data/samples/exchangerates_usd.json.

The other record-map shape, and a different one from CoinGecko's: 166 keys whose
values are bare floats, not objects. {"rates": {"EUR": 0.86, "JPY": 154.4, ...}}.

Worth having both. A record-map rule that turns keys into a column works for
CoinGecko, where each value is a row's worth of fields, and does nothing at all
here, where each value is a single number — a two-column table of key and value
would cost more than the JSON it replaced. This payload is the one that says
where that rule has to stop.

Today the compressor emits it as compact JSON with a note, at 0% and intact.

Ground truth is computed from the raw payload at run time, never hardcoded, so a
re-fetched sample keeps working. See src/checks_hn_stories.py for the shape.
"""


# --- PRESERVE checks --------------------------------------------------------

def x1_rate_count(data):
    return len(data["rates"])


def x2_base_currency(data):
    return data["base_code"]


def x3_eur_rate(data):
    return data["rates"]["EUR"]


def x4_jpy_rate(data):
    return data["rates"]["JPY"]


def x5_base_rate_is_one(data):
    """The base currency's own rate. A structural invariant of the payload."""
    return data["rates"][data["base_code"]]


def x6_all_rates_intact(data):
    """Every key and every float. The one check that would catch 165 of 166
    surviving — which is exactly the kind of loss a partial transform causes."""
    return data["rates"]


def x7_currency_codes(data):
    return sorted(data["rates"])


def x8_metadata_survives(data):
    return data["result"], data["time_last_update_utc"], data["provider"]


PRESERVE_CHECKS = [
    ("X1  rate count", x1_rate_count),
    ("X2  base currency", x2_base_currency),
    ("X3  EUR rate", x3_eur_rate),
    ("X4  JPY rate", x4_jpy_rate),
    ("X5  base currency's own rate", x5_base_rate_is_one),
    ("X6  all 166 rates intact", x6_all_rates_intact),
    ("X7  currency codes", x7_currency_codes),
    ("X8  response metadata survives", x8_metadata_survives),
]

# No *_url keys anywhere, so strip_boilerplate removes nothing.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "X9  roughly what is a euro worth in dollars here",
    "X10 name three currencies that are worth more than one US dollar",
]
