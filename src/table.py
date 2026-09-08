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

# Nested dicts become dotted columns: {"user": {"login": x}} -> "user.login".
FLATTEN_SEPARATOR = "."

MAX_FLATTEN_DEPTH = 2
# Measured 2026-09-08 on hn_stories.json. Depth 1 covered GitHub completely, but
# Algolia nests one level deeper: _highlightResult.title is itself a dict of
# {matchLevel, matchedWords, value}, so at depth 1 the whole thing sits in one
# JSON cell — 3,352 tokens, 16% of all cell content. Opened up, matchLevel and
# matchedWords are identical in all 30 rows and collapse into #const for free.
# That is the same payoff flattening had at depth 1, where it turned 12 constant
# columns into 21.
#
# Not unlimited: each level multiplies the column count, and a deeply nested
# object that varies per row would produce a wide, sparse table that costs more
# than the JSON cell it replaced. Raise it when a payload shows it pays.


MAX_RECORD_SEARCH_DEPTH = 4
# How deep to hunt for the records. Depth 1 covered GitHub (a bare array) and
# HackerNews ({"hits": [...]}), and stopping there quietly returned *nothing* for
# the very common {"data": {"items": [...]}} — JSON:API, GraphQL, and countless
# REST wrappers. Measured 2026-09-08: such a payload compressed by 0%.
#
# 4 covers every wrapper convention seen so far with room to spare, and the walk
# only descends through dict keys, so the cost is the number of nested objects,
# not the size of the data.


def find_record_array(data):
    """Locate the list-of-objects worth tabulating.

    Returns (rows, path) where path is the key sequence leading to the list, so
    decompression can rebuild whatever wrapped it. Returns (None, None) when
    there is nothing table-shaped here.

    Picks the LARGEST candidate rather than the first one found. "First" was an
    accident of dict ordering: a payload with a small incidental list before the
    real records would tabulate the wrong one. Size is measured as total cells
    (rows x keys per row), which is the closest cheap proxy for "how much data
    would a table save here".

    Ties break toward the shallower path, then alphabetically, so the same
    payload always produces the same document — an unstable choice would change
    the text, and any prompt cache built on it, for no reason.
    """
    candidates = []
    for path, value in _walk(data, (), MAX_RECORD_SEARCH_DEPTH):
        if _is_record_list(value):
            candidates.append((path, value, None))
        elif _is_record_map(value):
            key_column = free_key_name(value.values())
            candidates.append((path, map_to_rows(value, key_column), key_column))

    if not candidates:
        return None, None, None

    def rank(candidate):
        path, rows, _ = candidate
        cells = sum(len(row) for row in rows)
        return (-cells, len(path), path)

    path, rows, key_column = min(candidates, key=rank)
    return rows, list(path), key_column


def _is_record_map(value):
    """A dict whose values are all objects sharing one set of keys.

    {"bitcoin": {...}, "ethereum": {...}} is a table wearing a different hat:
    the records are the values and their identity is the key holding them.
    Common in price feeds, config APIs and anything Firebase-shaped. Measured
    2026-09-08 on coingecko_prices.json — eight objects of twelve identical keys,
    compressing by 0% because nothing looked for this shape. As a table: 32.5%.

    The values must be OBJECTS, not scalars. exchangerates_usd.json is the same
    dict-with-dynamic-keys shape but its values are bare floats, and a
    two-column key/value table costs 1.3% MORE than the JSON it would replace —
    there is no repeated key name to factor out when each record is one number.
    That payload is in the sample set precisely to hold this line.

    Identical key sets are required rather than merely similar ones: a union
    schema over ragged objects is fine for a list, where the absences are real
    data, but a dict of unrelated objects is not a table and tabulating it would
    produce a wide sparse mess that the savings gate would reject anyway.
    """
    if not isinstance(value, dict) or len(value) < MIN_RECORDS_IN_MAP:
        return False
    if not all(isinstance(record, dict) and record for record in value.values()):
        return False
    return len({tuple(sorted(record)) for record in value.values()}) == 1


MIN_RECORDS_IN_MAP = 2
# Same reasoning as MIN_ROWS_TO_TABULATE: below two records there is no repeated
# key name to factor out.


def free_key_name(records, preferred="_key"):
    """A column name for the map's keys that no record already uses."""
    taken = {key for record in records for key in record}
    name = preferred
    suffix = 2
    while name in taken:
        name = f"{preferred}{suffix}"
        suffix += 1
    return name


def map_to_rows(mapping, key_column):
    """{"btc": {...}} -> [{"_key": "btc", ...}]. Inverse: rows_to_map."""
    return [{key_column: key, **record} for key, record in mapping.items()]


def rows_to_map(rows, key_column):
    """Inverse of map_to_rows."""
    return {
        row[key_column]: {k: v for k, v in row.items() if k != key_column}
        for row in rows
    }


def _walk(value, path, depth):
    """Every (path, value) reachable through dict keys, down to `depth`.

    Only dict keys are followed. List *indices* are deliberately not walked:
    `#path` is a sequence of keys, so a list found inside another list could not
    be addressed on the way back, and the outer list is the one worth tabulating
    anyway.
    """
    yield path, value
    if depth > 0 and isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, path + (key,), depth - 1)


def _is_record_list(value):
    """A non-empty list whose items are all objects — the only table-able shape."""
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, dict) for item in value)
    )


def flatten_row(row, depth=None):
    """Nesting becomes dotted keys: {"user": {"login": x}} -> {"user.login": x}.

    Descends up to `depth` levels, defaulting to MAX_FLATTEN_DEPTH. A nested
    dict is only flattened when it can be put back together unambiguously — see
    can_flatten. Anything else, and anything past the depth limit, stays whole
    in one cell.

    The default is read here rather than written as `depth=MAX_FLATTEN_DEPTH` in
    the signature, where Python would bind it once at import. This project's
    whole method is to re-measure thresholds, so someone will eventually set
    table.MAX_FLATTEN_DEPTH = 3 to see what it buys — and with an import-time
    default that experiment silently keeps running at depth 2 and reports a
    number that means nothing.
    """
    if depth is None:
        depth = MAX_FLATTEN_DEPTH

    flat = {}
    for key, value in row.items():
        if depth > 0 and can_flatten(key, value):
            for inner_key, inner_value in flatten_row(value, depth - 1).items():
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
    """Inverse of flatten_row: "a.b.c" -> {"a": {"b": {"c": ...}}}.

    A parent dict is created only when at least one of its children is present,
    which is what preserves "this row genuinely had no pull_request key".

    Recursive, to match flatten_row: it peels one level per call, so a key still
    holding a separator after the split is nesting that has yet to be rebuilt.
    That reading is only unambiguous because can_flatten refuses to flatten a
    dict whose own keys contain the separator — so every dot present here was
    put there by flatten_row, never by the data.
    """
    row = {}
    nested = {}
    for key, value in flat.items():
        if FLATTEN_SEPARATOR in key:
            parent, inner_key = key.split(FLATTEN_SEPARATOR, 1)
            nested.setdefault(parent, {})[inner_key] = value
        else:
            row[key] = value

    for parent, children in nested.items():
        if parent in row:
            # "a" and "a.b" both present: the document wants a scalar and a dict
            # at the same key. Assigning would silently discard whichever came
            # first — and this function is also the standalone decompressor's
            # path, where there is no round-trip check downstream to notice.
            # Raising sends it to compress.py's fallback with a note instead.
            raise ValueError(
                f"cannot rebuild {parent!r}: present as both a value and a parent"
            )
        row[parent] = unflatten_row(children)
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
    """Equality that does not treat True as 1 or False as 0, at any depth.

    Python says True == 1 and False == 0, so a column holding True in one row
    and 1 in another would look constant and decompress to the wrong type.

    The check has to recurse, because scalar arrays are now a column shape in
    their own right: [True] == [1] under plain equality, so a children-style
    column holding [True] in one row and [1] in another collapsed into #const
    and gave row two the wrong type. The whole-payload round-trip check caught
    it and fell back to JSON, so nothing was ever corrupted — but this function
    is the one claiming to prevent the confusion, so it should actually prevent
    it rather than leave it to the net below.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same_value(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_value(a[k], b[k]) for k in a)
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


def build_table(rows, array_path=(), wrapper=None, key_column=None):
    """Rows of JSON objects -> the table shape that src/render.py writes out.

    The schema is the UNION of every row's keys, not the intersection. A key
    present in one row out of thirty still earns a column, marked nullable;
    rows without it carry MISSING in that cell. Intersection would silently
    delete the odd row's data, which is the opposite of the point.
    """
    flat_rows = [flatten_row(row) for row in rows]
    constants = constant_columns(flat_rows)
    varying_rows = remove_constants(flat_rows, constants)

    column_names = _ordered_column_names(varying_rows)

    columns = []
    for name in column_names:
        values = [row[name] for row in varying_rows if name in row]
        columns.append({
            "name": name,
            "type": column_type_name(values),
            "nullable": (
                len(values) < len(varying_rows)
                or any(value is None for value in values)
            ),
        })

    cells = [
        [row.get(name, MISSING) for name in column_names]
        for row in varying_rows
    ]

    return {
        "count": len(rows),
        "columns": columns,
        "cells": cells,
        "constants": constants,
        "array_path": list(array_path),
        # Non-None when the records came from a dict rather than a list: names
        # the column holding what used to be the dict key. rebuild_rows leaves
        # it in place; decompress._nest turns the rows back into a map.
        "key_column": key_column,
        # Whatever else sat beside the records in the wrapper object. An API
        # that returns {"hits": [...], "nbHits": 431, "page": 0} keeps its
        # metadata; dropping it would lose data the round-trip claims to
        # preserve.
        "wrapper": wrapper or {},
    }


def rebuild_rows(table):
    """Inverse of build_table: the table shape back into the original objects."""
    names = [column["name"] for column in table["columns"]]

    varying_rows = [
        {
            name: value
            for name, value in zip(names, row)
            if value is not MISSING
        }
        for row in table["cells"]
    ]

    flat_rows = restore_constants(varying_rows, table["constants"])
    return [unflatten_row(row) for row in flat_rows]


def _ordered_column_names(rows):
    """Most common columns first, ties broken alphabetically.

    Order is deterministic so the same payload always renders identically —
    an unstable column order would change the text (and any prompt cache built
    on it) for no reason.
    """
    counts = {}
    for row in rows:
        for name in row:
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts, key=lambda name: (-counts[name], name))


def column_type_name(values):
    """The type tag for a column, from the values actually present in it.

    bool is checked before int because Python makes bool a subclass of int, so
    an unguarded isinstance(True, int) would tag a boolean column as int and
    decode it back as 1.

    Arrays of scalars get their own types rather than falling through to "json".
    Measured 2026-09-08 on hn_stories.json: the `children` column (arrays of
    comment IDs) cost 33,310 tokens as JSON-inside-CSV — 81% of all cell content
    in the payload — because every element pays for `, ` separators and the
    whole array pays again for CSV quote-doubling. See scalar_array_type.
    """
    present = [value for value in values if value is not None]
    if not present:
        return "null"
    if all(isinstance(value, bool) for value in present):
        return "bool"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return "int"
    if all(isinstance(value, float) for value in present):
        return "float"
    if all(isinstance(value, str) for value in present):
        return "str"
    if all(isinstance(value, list) for value in present):
        array_type = scalar_array_type(present)
        if array_type:
            return array_type
    return "json"


def scalar_array_type(arrays):
    """Pick a space-separated array encoding, or None to leave it as JSON.

    Space separation only works when no element can contain the separator, so
    each type carries its own admission test and anything that fails one stays
    JSON. That is the whole safety argument: an unrepresentable column loses a
    saving, never a value.

      ints  — every element an int. `[1,2,3]` -> `1 2 3`
      dints — the same, written as first-value-then-differences:
              `[16582146,16582152,16582155]` -> `16582146 6 3`
      strs  — every element a non-empty string with no whitespace, and none
              starting with a backslash: a one-element array `["\\N"]` joins to
              exactly the cell text that means null, and would read back as
              None. The round-trip check catches that and falls back to JSON,
              but falling back loses the whole table over one cell, so refuse
              the column here instead.

    Delta form is exactly reversible for ANY list of ints (decoding is a running
    sum), so it is never a correctness question — only a token one. It is chosen
    when every array is non-decreasing, which is the structural signature of an
    ID or timestamp list: neighbours are close, so the differences are small
    integers where the originals were 8-digit ones. Measured on hn_stories.json:
    33,310 tokens as JSON -> 26,618 space-separated -> 13,525 delta-encoded.

    An unsorted int column keeps `ints`, where deltas could be larger than the
    values they replace.
    """
    if not any(arrays):
        # Every array in the column is empty, so there is no evidence of what it
        # holds — and `all(...)` over no elements is vacuously true, which would
        # hand the most exotic type, dints, to the column with the least
        # justification for it. hn_stories.json shipped exactly that: Algolia's
        # `matchedWords` is an array of strings, always empty in this sample, and
        # the header declared it `dints?`. Every cell round-tripped (they are all
        # \A), so no test caught it — but the document told the reading model
        # that a string column holds delta-encoded integers, which is the failure
        # the #legend line exists to prevent. Staying `json` costs nothing: `[]`
        # and `\A` are both two characters.
        return None

    if all(
        all(isinstance(item, int) and not isinstance(item, bool) for item in array)
        for array in arrays
    ):
        non_decreasing = all(
            all(a <= b for a, b in zip(array, array[1:])) for array in arrays
        )
        return "dints" if non_decreasing else "ints"

    if all(
        all(
            isinstance(item, str)
            and item
            and not item.startswith("\\")
            and not _has_whitespace(item)
            for item in array
        )
        for array in arrays
    ):
        return "strs"

    return None


def _has_whitespace(text):
    """Any whitespace at all, not just a space — a tab or newline in an element
    would survive the split() on the way back but not the join() on the way out."""
    return any(character.isspace() for character in text)
