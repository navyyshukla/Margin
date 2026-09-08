r"""Write the table shape out as text, and read it back.

(Raw docstring — this one documents backslash escapes, and \N is a unicode-name
escape in a normal Python string.)

Writer and reader live in one file on purpose. They are inverses, and two
inverses in separate files drift apart — someone fixes an escaping bug on one
side and the other silently disagrees. Here they are edited in the same diff.

The format, `#margin/v1`:

    #margin/v1
    #const{"state": "open", "locked": false, "reactions.laugh": 0}
    [30]{number:int,title:str,body:str,draft:bool?}
    37537,"[compiler] Preserve JSX pragmas","## Summary...",false
    37534,"[DevTools Bug]: Inspect button",\N,

Why CSV rather than something cleverer: models have seen an enormous amount of
CSV in training, and Python's csv module already solves quoting correctly. An
invented format would need both the model and us to learn its escaping.

Cell encoding, and why each case exists:

    key absent in this row   ->  (empty)     the row genuinely lacked the key
    None                     ->  \N          borrowed from Postgres COPY/MySQL
    "" (empty string)        ->  \E          so it cannot be read back as absent
    [] (empty array)         ->  \A          same collapse, same fix
    True / False             ->  true/false
    int / float              ->  str(value)
    array of scalars         ->  space-separated (column types ints/dints/strs)
    anything else            ->  compact JSON
    string starting with \   ->  one extra leading backslash

Absent and null must not collapse into the same cell: after boilerplate
stripping a PR carries pull_request={"merged_at": None} while a plain issue has
no pull_request key at all, and Q4/Q10 tell them apart by exactly that.
"""

import csv
import io
import json

from table import MISSING

FORMAT_MARKER = "#margin/v1"
LEGEND_PREFIX = "#legend "
CONST_PREFIX = "#const"
PATH_PREFIX = "#path"
WRAP_PREFIX = "#wrap"
KEYED_PREFIX = "#keyed "
# Present when the records came from a dict rather than a list: names the column
# holding what was the dict key. Without it the rows would come back as a list
# and the identities would be data in a column rather than the keys they were.

NULL_CELL = "\\N"
EMPTY_STRING_CELL = "\\E"
EMPTY_ARRAY_CELL = "\\A"
# [] would otherwise render as the empty string, which already means "key absent
# in this row" — the same collapse \E exists to prevent for "".

# Column types whose cells are space-separated scalars instead of JSON. Decided
# in table.scalar_array_type; written and read here.
ARRAY_TYPES = ("ints", "dints", "strs")


LEGEND_ENTRIES = [
    # (what to look for, what to say). Order is the order they are printed.
    #
    # Wording tuned by the cold read of 2026-09-08 (docs/cold-read-2026-09-08.md).
    # The reader got every answer right but had to *infer* two things: that the
    # first dints token is absolute rather than a delta from zero, and that an
    # empty cell means the key is absent — which is the distinction Q4/Q10 use to
    # tell pull requests from plain issues, so leaving it to inference is not
    # acceptable. Both are now stated.
    ("dints", "col:dints = ints as first-value-then-differences, first is absolute (10 3 2 -> 10,13,15)"),
    ("ints", "col:ints/strs = space-separated list"),
    ("strs", "col:ints/strs = space-separated list"),
    (NULL_CELL, r"\N = null"),
    (EMPTY_STRING_CELL, r"\E = empty string"),
    (EMPTY_ARRAY_CELL, r"\A = empty array"),
]


def legend_for(table, encoded_rows):
    """The one-line key to any non-obvious encoding this document actually uses.

    Token savings are worthless if the reader misreads the result, and `dints`
    is the case that makes this urgent: a delta-encoded ID list renders as
    `16582146 6 3 2`, and a model that takes those at face value answers with
    comment IDs that do not exist. Confidently wrong is worse than uncompressed.

    Every other part of the format is either self-evident (CSV rows under a
    named header) or spelled out in the header itself, so the legend covers only
    the encodings that cannot be guessed. Entries are emitted only when the
    document contains them — a payload with no arrays pays nothing for arrays.
    Measured 2026-09-08: 44 tokens on hn_stories.json, 7 on github_issues.json.

    Takes the already-encoded rows rather than re-encoding: the cells are the
    expensive part of rendering (every `children` array gets delta-encoded), and
    sniffing them a second time doubled that cost for nothing.

    Sentinels are matched per cell and in full, never as a substring of the
    document. A `str` value beginning with a backslash is escaped by doubling
    it, so "\\Name" becomes the cell `\\\\Name` — which *contains* `\\N` while
    meaning nothing of the sort, and a substring test put "\\N = null" in the
    legend of a document with no nulls in it.
    """
    types = {column["type"] for column in table["columns"]}
    cells = {cell for row in encoded_rows for cell in row}

    seen = []
    for marker, description in LEGEND_ENTRIES:
        present = marker in types if marker in ARRAY_TYPES else marker in cells
        if present and description not in seen:
            seen.append(description)

    # Stated whenever any cell is empty, and separately from the sentinels
    # because it is the absence of a marker rather than a marker — the one thing
    # in the format with nothing visible to point at.
    if any(cell == "" for row in encoded_rows for cell in row):
        seen.append("empty cell = key absent in that row")

    # #keyed is a structural line rather than a cell encoding, but it is the
    # least guessable thing in the format: without it a reader sees an ordinary
    # column called _key and no reason to think the rows were ever a dict.
    if table.get("key_column"):
        seen.append(
            f"#keyed = rows came from an object; column {table['key_column']} "
            "holds each record's key"
        )

    # a.b was the only convention the 2026-09-08 cold reader got right purely by
    # recognising dotted-path notation from elsewhere — the document itself never
    # said it. A reader without that background reads `continent.name` as a
    # column literally called "continent.name".
    if any("." in column["name"] for column in table["columns"]):
        seen.append("col a.b = nested object, i.e. {\"a\": {\"b\": ...}}")

    return "; ".join(seen)


def render(table):
    """Turn the table shape into the `#margin/v1` document."""
    types = [column["type"] for column in table["columns"]]
    encoded_rows = [
        [encode_cell(value, type_name) for value, type_name in zip(row, types)]
        for row in table["cells"]
    ]

    lines = [FORMAT_MARKER]

    legend = legend_for(table, encoded_rows)
    if legend:
        lines.append(LEGEND_PREFIX + legend)

    # Only written when the records were nested under a key, which keeps the
    # common bare-array case one line shorter.
    if table["array_path"]:
        lines.append(PATH_PREFIX + json.dumps(table["array_path"], separators=(",", ":")))
    if table.get("key_column"):
        lines.append(KEYED_PREFIX + json.dumps(table["key_column"], separators=(",", ":")))
    if table.get("wrapper"):
        lines.append(WRAP_PREFIX + json.dumps(table["wrapper"], separators=(",", ":")))

    if table["constants"]:
        lines.append(CONST_PREFIX + json.dumps(table["constants"], separators=(",", ":")))

    header_cols = ",".join(
        f"{c['name']}:{c['type']}{'?' if c['nullable'] else ''}"
        for c in table["columns"]
    )
    lines.append(f"[{table['count']}]{{{header_cols}}}")

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in encoded_rows:
        writer.writerow(row)

    return "\n".join(lines) + "\n" + buffer.getvalue()


def parse(text):
    """Read a `#margin/v1` document back into the table shape."""
    lines = text.split("\n")
    if not lines or lines[0] != FORMAT_MARKER:
        raise ValueError(f"not a {FORMAT_MARKER} document")

    index = 1
    # Written for the model reading the document, not for this parser — the
    # header already carries every type it needs. Skipped, not required.
    if index < len(lines) and lines[index].startswith(LEGEND_PREFIX):
        index += 1

    array_path = []
    if index < len(lines) and lines[index].startswith(PATH_PREFIX):
        array_path = json.loads(lines[index][len(PATH_PREFIX):])
        index += 1

    key_column = None
    if index < len(lines) and lines[index].startswith(KEYED_PREFIX):
        key_column = json.loads(lines[index][len(KEYED_PREFIX):])
        index += 1

    wrapper = {}
    if index < len(lines) and lines[index].startswith(WRAP_PREFIX):
        wrapper = json.loads(lines[index][len(WRAP_PREFIX):])
        index += 1

    constants = {}
    if index < len(lines) and lines[index].startswith(CONST_PREFIX):
        constants = json.loads(lines[index][len(CONST_PREFIX):])
        index += 1

    count, columns = parse_header(lines[index])
    index += 1

    # The remaining lines are CSV. Hand them back to the csv module whole rather
    # than line by line: a quoted cell may legally contain newlines, so "one
    # line" and "one row" are not the same thing.
    csv_text = "\n".join(lines[index:])
    cells = []
    if not columns:
        # Every column was constant, so each row rendered as an empty line — and
        # an empty line is indistinguishable from the trailing newline skipped
        # below. The count in the header is the only surviving record of how
        # many rows there were, which is what makes it worth writing down.
        cells = [[] for _ in range(count)]
    for row in csv.reader(io.StringIO(csv_text)):
        if not row:
            continue  # trailing newline at end of document
        cells.append([
            decode_cell(value, column["type"])
            for value, column in zip(row, columns)
        ])

    return {
        "count": count,
        "columns": columns,
        "cells": cells,
        "constants": constants,
        "array_path": array_path,
        "key_column": key_column,
        "wrapper": wrapper,
    }


def parse_header(line):
    """`[30]{number:int,title:str?}` -> (30, [{name, type, nullable}, ...])."""
    close_bracket = line.index("]")
    count = int(line[1:close_bracket])

    inner = line[line.index("{") + 1:line.rindex("}")]
    if not inner:
        # `[N]{}` — every column turned out constant, so the table is a row
        # count and nothing else. "".split(",") gives [""], which then fails to
        # split on ":", so this needs its own case. Depth-2 flattening made it
        # reachable: more columns get opened up, so more of them can be
        # constant, and the whole payload silently lost its table when they all
        # were.
        return count, []

    columns = []
    for spec in inner.split(","):
        name, type_name = spec.rsplit(":", 1)
        nullable = type_name.endswith("?")
        columns.append({
            "name": name,
            "type": type_name.rstrip("?"),
            "nullable": nullable,
        })
    return count, columns


def encode_cell(value, type_name=None):
    """One JSON value -> the text that goes in a CSV cell.

    type_name is the column's declared type. It only matters for the array
    types, where the column — not the individual value — decides the encoding.
    """
    if value is MISSING:
        return ""
    if value is None:
        return NULL_CELL
    if type_name in ("json", "num"):
        # A "json" column is the mixed-type column: its values have no single
        # Python type, so the ONLY thing decode_cell can do is json.loads. That
        # forces every value here to be written as JSON, strings included.
        #
        # Dispatching on the value's own type instead — which is what the rest
        # of this function does — silently disagrees with the decoder. A string
        # "a,b" was written bare and crashed json.loads on the way back; worse,
        # the string "true" was written as `true` and read back as the boolean,
        # and "3.0" as the float. The round-trip check caught both and fell back
        # to JSON, so no data was ever lost, but any payload with a mixed column
        # containing strings lost its table entirely — and mixed columns are
        # common in real APIs. Found by src/property_test.py, 2026-09-08.
        return json.dumps(value, separators=(",", ":"))
    if isinstance(value, bool):
        # Checked before int: in Python, True is an int.
        return "true" if value else "false"
    if isinstance(value, str):
        if value == "":
            return EMPTY_STRING_CELL
        if value.startswith("\\"):
            return "\\" + value
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if type_name in ARRAY_TYPES:
        if not value:
            return EMPTY_ARRAY_CELL
        if type_name == "dints":
            return " ".join(str(n) for n in to_deltas(value))
        return " ".join(str(item) for item in value)
    return json.dumps(value, separators=(",", ":"))


def to_deltas(numbers):
    """[16582146, 16582152, 16582155] -> [16582146, 6, 3]. First value absolute."""
    return [numbers[0]] + [b - a for a, b in zip(numbers, numbers[1:])]


def from_deltas(numbers):
    """Inverse of to_deltas: a running sum."""
    running = numbers[0]
    restored = [running]
    for delta in numbers[1:]:
        running += delta
        restored.append(running)
    return restored


def decode_cell(text, type_name):
    """Inverse of encode_cell. Decodes by the column's declared type.

    Never guess the type from the text — "3.0" and "3" would round-trip to the
    wrong Python type, and "true" could be a string that happens to say true.
    """
    if text == "":
        return MISSING
    if text == NULL_CELL:
        return None
    if text == EMPTY_STRING_CELL:
        return ""
    if text == EMPTY_ARRAY_CELL:
        return []

    if type_name in ARRAY_TYPES:
        if type_name == "strs":
            return text.split(" ")
        numbers = [int(part) for part in text.split(" ")]
        return from_deltas(numbers) if type_name == "dints" else numbers
    if type_name == "str":
        if text.startswith("\\\\"):
            return text[1:]
        return text
    if type_name == "bool":
        return text == "true"
    if type_name == "int":
        return int(text)
    if type_name == "float":
        return float(text)
    # "num" and "json" both go through json.loads, which is what keeps 67514 an
    # int and 2138.02 a float in the same column.
    return json.loads(text)
