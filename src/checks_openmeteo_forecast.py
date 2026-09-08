"""Eval checks for data/samples/openmeteo_forecast.json.

This payload is here because it is the shape the compressor CANNOT help, and
that needs a test as much as the shapes it can.

Open-Meteo is already columnar: {"hourly": {"time": [...], "temperature_2m":
[...]}} — four parallel arrays of 168 scalars, keyed once each. That is
essentially what the table format produces, so there is nothing left to factor
out. There is no list of objects anywhere, so find_record_array returns nothing
and the compressor emits JSON with a note saying so.

The assertion is therefore "0% and completely intact", not "N% saved". A rule
that started reshaping this payload would be inventing work, and these checks
would catch it losing data while doing so.

Ground truth is computed from the raw payload at run time, never hardcoded, so a
re-fetched sample keeps working. See src/checks_hn_stories.py for the shape.
"""


def hourly(data):
    return data["hourly"]


# --- PRESERVE checks --------------------------------------------------------

def o1_reading_count(data):
    return len(hourly(data)["time"])


def o2_all_series_same_length(data):
    """Parallel arrays only mean anything if they stay aligned."""
    return len({len(values) for values in hourly(data).values()}) == 1


def o3_first_and_last_timestamp(data):
    times = hourly(data)["time"]
    return times[0], times[-1]


def o4_max_temperature_and_when(data):
    temps = hourly(data)["temperature_2m"]
    peak = max(range(len(temps)), key=lambda i: temps[i])
    return hourly(data)["time"][peak], temps[peak]


def o5_min_temperature(data):
    return min(hourly(data)["temperature_2m"])


def o6_temperature_series_intact(data):
    """The whole series, element for element. A float silently becoming an int,
    or a reordered array, shows up here and nowhere else."""
    return hourly(data)["temperature_2m"]


def o7_units(data):
    """Units live beside the data, not in it — drop them and every number in the
    payload becomes ambiguous."""
    return data["hourly_units"]


def o8_location_metadata(data):
    return data["latitude"], data["longitude"], data["elevation"], data["timezone"]


def o9_humidity_at_first_hour(data):
    return hourly(data)["relative_humidity_2m"][0]


PRESERVE_CHECKS = [
    ("O1  hourly reading count", o1_reading_count),
    ("O2  all series are the same length", o2_all_series_same_length),
    ("O3  first and last timestamp", o3_first_and_last_timestamp),
    ("O4  peak temperature and its hour", o4_max_temperature_and_when),
    ("O5  minimum temperature", o5_min_temperature),
    ("O6  full temperature series intact", o6_temperature_series_intact),
    ("O7  units survive", o7_units),
    ("O8  location metadata", o8_location_metadata),
    ("O9  humidity at the first hour", o9_humidity_at_first_hour),
]

# No *_url keys anywhere, so strip_boilerplate removes nothing.
REMOVED_CHECKS = []

MANUAL_QUESTIONS = [
    "O10 roughly what is the temperature trend over the week",
    "O11 which day looks windiest",
]
