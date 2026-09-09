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
import store as store_module
from table import rebuild_rows, rows_to_map


def decompress(text, store=None):
    """Compressed document -> JSON data.

    Anything that is not a #margin/v1 document is plain JSON — that is what the
    savings gate emits when a table would not have been worth it, so this has
    to read both.

    `store` is required only by a document carrying a #store line. Without it
    this raises, rather than handing back rows with Handle objects sitting where
    values should be: a caller that got those would serialise them to something
    meaningless and never learn the payload was incomplete. A store is the first
    thing in this pipeline that can lose half a document while looking fine.
    """
    if not text.startswith(render.FORMAT_MARKER):
        return json.loads(text)

    table = render.parse(text)

    doc_id = table.get("store")
    if doc_id:
        if store is None:
            raise ValueError(
                f"this document's values live in a store ({doc_id}) and no store "
                f"was given — decompressing without it would silently drop them"
            )
        store_module.restore_handles(table, store.read_index(doc_id), store)

    rows = rebuild_rows(table)

    # Records that came from a dict go back into one. The key column carries
    # what the dict keys were; leaving it as a column would turn an object's
    # identity into an ordinary field.
    key_column = table.get("key_column")
    records = rows_to_map(rows, key_column) if key_column else rows

    return _nest(records, table["array_path"], table.get("wrapper") or {})


def _nest(records, array_path, wrapper):
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
        return records

    node = wrapper
    for key in array_path[:-1]:
        node = node[key]
    node[array_path[-1]] = records
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
