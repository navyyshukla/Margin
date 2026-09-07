"""Reshape a list of similar JSON objects into table form, and back again.

This is the pure-data half of the compressor: it decides the table's SHAPE but
renders no text. `src/render.py` turns the shape into the actual document. The
split exists so the shape logic can be tested and measured without arguing about
formatting, and so a second output format never means rewriting this file.

Every function here has an inverse living beside it, because the whole design
rests on one claim: whatever we do to the data, we can undo exactly.

MISSING vs None is the distinction that makes that claim true. "This row had no
such key" and "this row had the key, set to null" are different facts, and the
eval questions depend on the difference — after boilerplate stripping, a PR's
`pull_request` is {"merged_at": None} while a plain issue has no `pull_request`
key at all, which is exactly how Q4 and Q10 tell them apart.
"""

MISSING = object()  # sentinel: this row had no such key (never appears in JSON)

# Nested dicts are flattened one level only: {"user": {"login": x}} -> "user.login".
# Deeper nesting stays a whole value in one cell — depth-1 covers every nested
# object in the sample payloads, and each extra level multiplies the column count.
FLATTEN_SEPARATOR = "."


def find_record_array(data):
    """Locate the list-of-objects worth tabulating.

    Returns (rows, path) where path is the key sequence leading to the list, so
    decompression can rebuild whatever wrapped it. Returns (None, None) when
    there is nothing table-shaped here.

    The wrapper case is not hypothetical: GitHub returns a bare array, but plenty
    of APIs (HackerNews/Algolia, for one) return {"hits": [...]} with metadata
    alongside. Checking only isinstance(data, list) would quietly skip those.
    """
    if _is_record_list(data):
        return data, []

    if isinstance(data, dict):
        # Only descend one level: a records list is normally a top-level field,
        # and searching deeper risks tabulating something incidental.
        for key, value in data.items():
            if _is_record_list(value):
                return value, [key]

    return None, None


def _is_record_list(value):
    """A non-empty list whose items are all objects — the only table-able shape."""
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, dict) for item in value)
    )


def flatten_row(row):
    """One level of nesting becomes dotted keys: {"user": {"login": x}} -> {"user.login": x}.

    A nested dict is only flattened when it can be put back together
    unambiguously — see can_flatten. Anything else stays whole.
    """
    flat = {}
    for key, value in row.items():
        if can_flatten(key, value):
            for inner_key, inner_value in value.items():
                flat[f"{key}{FLATTEN_SEPARATOR}{inner_key}"] = inner_value
        else:
            flat[key] = value
    return flat


def can_flatten(key, value):
    """Is this value a dict we can flatten and later rebuild exactly?

    Three things make a dict unsafe to flatten:
      - empty {} flattens to no columns at all, so unflattening cannot tell it
        apart from an absent key
      - a dotted key or dotted parent makes the split ambiguous on the way back
      - a non-dict has no inner keys to lift
    """
    if not isinstance(value, dict) or not value:
        return False
    if FLATTEN_SEPARATOR in key:
        return False
    return all(FLATTEN_SEPARATOR not in inner_key for inner_key in value)


def unflatten_row(flat):
    """Inverse of flatten_row: "user.login" -> {"user": {"login": ...}}.

    A parent dict is created only when at least one of its children is present,
    which is what preserves "this row genuinely had no pull_request key".
    """
    row = {}
    for key, value in flat.items():
        if FLATTEN_SEPARATOR in key:
            parent, inner_key = key.split(FLATTEN_SEPARATOR, 1)
            row.setdefault(parent, {})[inner_key] = value
        else:
            row[key] = value
    return row


def constant_columns(flat_rows):
    """Columns present in every row with the same value throughout.

    These are the compressor's cheapest win: state the value once instead of
    repeating it per row. It subsumes what would otherwise be several
    special-case rules — a field that is null everywhere, a boolean that is
    always false, a reaction count that is always zero are all just columns
    with one distinct value.

    Requiring presence in EVERY row matters: a column missing from some rows
    carries the absence as information, and folding it into a constant would
    silently invent values for those rows.
    """
    if not flat_rows:
        return {}

    constants = {}
    for key in flat_rows[0]:
        if not all(key in row for row in flat_rows):
            continue
        first = flat_rows[0][key]
        if all(_same_value(row[key], first) for row in flat_rows):
            constants[key] = first
    return constants


def _same_value(a, b):
    """Equality that does not treat True as 1 or False as 0.

    Python says True == 1 and False == 0, so a column holding True in one row
    and 1 in another would look constant and decompress to the wrong type.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def remove_constants(flat_rows, constants):
    """Drop the constant columns from every row; they live in the header instead."""
    return [
        {k: v for k, v in row.items() if k not in constants}
        for row in flat_rows
    ]


def restore_constants(flat_rows, constants):
    """Inverse of remove_constants: put every constant back into every row."""
    return [{**row, **constants} for row in flat_rows]
