"""Turn a compressed document back into JSON.

Deliberately thin — the real work is in render.parse (text -> table shape) and
table.rebuild_rows (table shape -> objects). This just joins them and puts the
records back wherever they were found.

What it does NOT restore: the `*_url` and id fields removed by
strip_boilerplate. That stage is lossy on purpose, so the round-trip is
measured against its output, never against the original file. See src/table.py.

Usage: python src/decompress.py compressed.txt
"""

import json
import sys

import render
from table import rebuild_rows


def decompress(text):
    """Compressed document -> JSON data.

    Anything that is not a #margin/v1 document is plain JSON — that is what the
    savings gate emits when a table would not have been worth it, so this has
    to read both.
    """
    if not text.startswith(render.FORMAT_MARKER):
        return json.loads(text)

    table = render.parse(text)
    rows = rebuild_rows(table)
    return _nest(rows, table["array_path"], table.get("wrapper") or {})


def _nest(rows, array_path, wrapper):
    """Put the records back where they were found, inside the skeleton.

    The wrapper is the whole document minus the rows (see compress.skeleton), so
    everything that surrounded the records comes back with them — an API
    returning {"hits": [...], "nbHits": 431, "page": 0} keeps its metadata, which
    the round-trip check would otherwise catch us losing.

    Walks the path rather than merging at the top. Building {"data": {"items":
    rows}} and merging that over the wrapper would replace the wrapper's whole
    "data" value, silently dropping every sibling of "items" inside it.
    """
    if not array_path:
        return rows

    node = wrapper
    for key in array_path[:-1]:
        node = node[key]
    node[array_path[-1]] = rows
    return wrapper


def main():
    if len(sys.argv) != 2:
        print("usage: python src/decompress.py <path-to-compressed-file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        text = f.read()

    print(json.dumps(decompress(text)))


if __name__ == "__main__":
    main()
