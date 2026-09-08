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


def build_table(rows, array_path=(), wrapper=None):
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
