"""Project what a content store would save, before any of it is built.

The store's premise, from docs/shapes.md: 60% of what Margin still emits is bulk
content no lossless rule reduces, so the way forward is to stop putting it in the
prompt — each bulk cell becomes a handle, the content lives in a store on disk,
and the model fetches only what a question actually needs.

That premise has been measured wrong once already. The first attempt counted only
prose over 200 characters, missed jsonplaceholder's short bodies entirely,
excluded hn_stories' `children` for not being prose, and concluded the store
"targets a problem seven of eight payloads do not have". The correction is in
docs/shapes.md. This script exists so the number stops being an argument and
starts being an output.

Four decisions, each of which some earlier measurement here got wrong:

- **Measure cells, not a category.** "Bulk content" is a human judgement about
  what a field means, and judging it is exactly how the first measurement went
  wrong. Here nothing is classified: every cell is priced against a handle, and
  whatever wins is what the store would take. "Bulk" becomes a result.

- **Price the document that would really be written.** Cells are encoded with
  render.encode_cell and quoted with the same csv.writer render.py uses.
  docs/thresholds.md records the #dict cost model getting this wrong — pricing a
  bare `str` cell with json.dumps charged three characters where the document
  holds one, which over-charged the status quo and made every dictionary look
  better than it was.

- **Measure after #const and #dict, never before.** build_table has already
  factored out constants and dictionary columns by the time this sees a cell, so
  no saving is claimed that an existing rule has already taken. A projection
  against the raw payload would double-count and look far better than it is.

- **Render the document; do not model it.** Every figure below is
  `token_count()` of a document render.render() actually produced with handles
  substituted — not a sum of per-cell arithmetic. The first version of this file
  did the arithmetic, and checking it against a real render disagreed by up to
  86 tokens (2.9% on jsonplaceholder), because tiktoken is context dependent: a
  cell priced alone does not cost what it costs inside a CSV line. It also
  charged the fixed #store cost to payloads that store nothing and would not
  carry the line at all. Rule 1's shape — run the thing rather than trusting a
  model of it — and Rule 5's, on a number that had existed for ten minutes.

Usage:  python src/measure_store.py [data/samples/*.json]
        (no arguments: every payload in data/samples/)
"""

import csv
import glob
import io
import json
import os
import sys

import render
import store as store_module
from compress import COMPACT, skeleton, strip_boilerplate
from table import build_table, find_record_array
from tokens import token_count

# A handle has to be long enough that two different bodies never collide into
# one — a collision is silent data loss, which is the failure this project's
# first decision ("never delete-and-hope") rules out. 12 hex characters is 48
# bits; across the ~10^4 distinct cells in this sample set the birthday
# probability is under 10^-9. Priced rather than assumed: report_encodings()
# shows what every other width would buy, so this is a choice and not a default.
HANDLE_SAMPLE = "@3f9a2c1b7e4d"

# What a document pays once for carrying a store: the #store line naming where
# the content lives, plus the legend entry saying that @xxx is a fetch and not a
# value. Charged only to documents that actually store something — a document
# with no handles carries neither line.
STORE_LINE = '#store{"kind":"file","root":".margin/store"}'
STORE_LEGEND = "@xxx = fetch this id from the store; the value is not in this document"

# The default qualifying bar, in tokens saved by one cell. Chosen from the curve
# report_thresholds() prints rather than picked: see docs/thresholds.md.
MIN_STORE_SAVING = 20


def csv_field_cost(text):
    """Tokens for one cell as the document writes it, quoting included.

    Used only to decide whether a cell qualifies — a local, per-cell question.
    The document's total is never summed from this; see render_with_store.

    render.py writes cells through csv.writer, which wraps a field in quotes and
    doubles interior quotes when it contains a comma, a quote or a newline. Prose
    cells hit all three, so pricing the bare text under-charges exactly the cells
    this measurement is about.
    """
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow([text])
    return token_count(buffer.getvalue())


def lure(cell_id, text, chars=48, show_tokens=True):
    """A handle that tells the reader what it is choosing whether to fetch.

    `\\@0001` on its own says nothing at all. A reader cannot tell whether it
    wants that value, so it either fetches every handle — which costs more than
    never having stored them — or fetches none and answers from the columns
    around it, which is the failure mode that would make this whole stage worse
    than useless. Headroom's equivalent marker carries a description and an item
    count for exactly this reason.

    The id stays fixed-width and leading, so the decoder still reads it as
    text[2:6] with no delimiter parsing; everything after is for the reader.
    """
    head = " ".join(text.split())[:chars]
    size = f"[{token_count(text)}t] " if show_tokens else ""
    return f"{render.HANDLE_PREFIX}{cell_id}{size}{head}"


def render_with_store(table, handle=HANDLE_SAMPLE, min_saving=MIN_STORE_SAVING,
                      handle_for=None):
    """Render the document this table would produce with a store behind it.

    Returns (cells_stored, document_tokens). This is the only place a projected
    document size comes from, so there is no second arithmetic path that could
    disagree with it — Rule 4's shape, applied to a measurement rather than to an
    encoder.

    Deduplication deliberately changes nothing here. The store is content
    addressed, so two identical bodies share one entry *on disk* — but the
    document still carries a handle at each occurrence, and the document is what
    costs prompt tokens. Crediting the document with dedup would be the same
    class of error as measuring against json.dumps: an economy the reader never
    sees.
    """
    handle_cost = csv_field_cost(handle)
    types = [column["type"] for column in table["columns"]]

    stored = 0
    cells = []
    for row in table["cells"]:
        out_row = []
        for value, type_name in zip(row, types):
            text = render.encode_cell(value, type_name)
            if csv_field_cost(text) - handle_cost >= min_saving:
                stored += 1
                # The handle is a plain string, so encode_cell writes it
                # verbatim and csv.writer quotes it exactly as it would any
                # other cell. Nothing here bypasses the real renderer.
                #
                # The qualifying decision above still uses the *bare* handle's
                # cost, deliberately: whether a cell is worth storing is a
                # property of the cell, and letting a longer lure disqualify
                # cells would silently shrink what gets stored while measuring
                # the lure's price. One variable at a time.
                out_row.append(
                    handle_for(store_module.format_id(stored), text)
                    if handle_for else handle
                )
            else:
                out_row.append(value)
        cells.append(out_row)

    with_store = dict(table)
    with_store["cells"] = cells
    document = render.render(with_store)

    overhead = 0
    if stored:
        overhead = token_count(STORE_LINE) + token_count(STORE_LEGEND)
    return stored, token_count(document) + overhead


def measure(path):
    """One payload -> the numbers, or a reason there are none."""
    with open(path, encoding="utf-8") as handle:
        original = handle.read()

    stripped = strip_boilerplate(json.loads(original))
    rows, array_path, key_column = find_record_array(stripped)

    result = {"name": os.path.basename(path), "raw": token_count(original)}

    if rows is None or len(rows) < 2:
        # openmeteo (already columnar) and exchangerates (a map of scalars) reach
        # here. They emit plain JSON today, so there are no cells to price — not
        # a gap in this measurement, the same correct outcome docs/shapes.md
        # records for them.
        result["reason"] = "no record array — emits JSON, no cells to price"
        return result

    table = build_table(rows, array_path, skeleton(stripped, array_path), key_column)
    stored, projected = render_with_store(table)

    result.update({
        "reason": None,
        "table": table,
        "out": token_count(render.render(table)),
        "cells": sum(len(row) for row in table["cells"]),
        "stored": stored,
        "projected": projected,
        # Everything riding in #wrap is raw JSON, not cells, so a cell-level
        # store cannot touch it. Reported because on pokeapi_ditto it is most of
        # the payload, and leaving it out of view would overstate the coverage
        # of this projection.
        "wrap": token_count(json.dumps(table.get("wrapper") or {}, separators=COMPACT)),
    })
    return result


def report(results):
    """Per payload, in the same unit and shape as docs/shapes.md."""
    print(f"{'payload':<30}{'raw':>9}{'out now':>9}{'out+store':>11}"
          f"{'now':>8}{'with store':>12}{'cells':>8}{'stored':>8}")
    raw = out = projected = 0

    for item in results:
        if item["reason"]:
            print(f"{item['name']:<30}{item['raw']:>9}{'—':>9}{'—':>11}"
                  f"{'0.0%':>8}{'0.0%':>12}   {item['reason']}")
            raw += item["raw"]
            out += item["raw"]
            projected += item["raw"]
            continue

        print(f"{item['name']:<30}{item['raw']:>9}{item['out']:>9}{item['projected']:>11}"
              f"{1 - item['out'] / item['raw']:>7.1%}"
              f"{1 - item['projected'] / item['raw']:>12.1%}"
              f"{item['cells']:>8}{item['stored']:>8}")

        raw += item["raw"]
        out += item["out"]
        projected += item["projected"]

    print(f"{'TOTAL':<30}{raw:>9}{out:>9}{projected:>11}"
          f"{1 - out / raw:>7.1%}{1 - projected / raw:>12.1%}")


def report_thresholds(results):
    """Saving against the qualifying bar, whole set, every figure a real render.

    The bar is the decision this script exists to inform. A handle that saves one
    token is arithmetically a win and a practical loss: it costs the model a
    fetch to recover a value it could have read in place. MIN_DICT_SAVING was
    settled with a table rather than a guess, and so is this.
    """
    print("\nqualifying bar, whole sample set")
    print(f"{'min tokens saved':>18}{'cells stored':>14}{'set total':>12}")
    raw = sum(item["raw"] for item in results)

    for minimum in (1, 5, 10, 20, 50, 100, 200):
        stored = 0
        total = 0
        for item in results:
            if item["reason"]:
                total += item["raw"]
                continue
            count, size = render_with_store(item["table"], min_saving=minimum)
            stored += count
            total += size
        print(f"{minimum:>18}{stored:>14}{1 - total / raw:>11.1%}")


def report_encodings(results):
    """What the handle's own shape costs, whole set, at the default bar.

    `@0001` is a different design, not a shorter hash: an id counted per document
    rather than a content address computed over every payload. It cannot dedupe
    across documents and its keys mean nothing outside the document that names
    them — but if it is worth thousands of tokens, that trade should be made
    deliberately rather than lost by defaulting to a hash.
    """
    print("\nhandle encoding, whole sample set")
    print(f"{'encoding':>20}{'example':>20}{'set total':>12}")
    raw = sum(item["raw"] for item in results)

    candidates = [(f"hex-{w}", "@" + "3f9a2c1b7e4d5a6b"[:w]) for w in (4, 6, 8, 12, 16)]
    candidates.append(("per-document id", "@0001"))

    for label, sample in candidates:
        total = 0
        for item in results:
            if item["reason"]:
                total += item["raw"]
                continue
            _, size = render_with_store(item["table"], handle=sample)
            total += size
        print(f"{label:>20}{sample:>20}{1 - total / raw:>11.1%}")


def report_lures(results):
    """What a lure costs, whole set. The reader gains; the prompt pays."""
    print("\nhandle lure (whole sample set, default bar)")
    print(f"{'variant':>34}{'set total':>12}{'vs bare':>10}")
    raw = sum(item["raw"] for item in results)

    def total(handle_for):
        out = 0
        for item in results:
            if item["reason"]:
                out += item["raw"]
                continue
            out += render_with_store(item["table"], handle_for=handle_for)[1]
        return out

    bare = total(None)
    variants = [
        # The baseline must be the handle actually shipped, not HANDLE_SAMPLE's
        # hex-12 default -- comparing lures against a *different* encoding made
        # "tokens only" look 761 tokens CHEAPER than bare, which is Rule 5's
        # shape again: a baseline that is not what it claims to be.
        ("bare  \\@0001", lambda i, t: lure(i, t, chars=0, show_tokens=False)),
        ("tokens only  \\@0001[142t]", lambda i, t: lure(i, t, chars=0)),
        ("prefix 32", lambda i, t: lure(i, t, chars=32, show_tokens=False)),
        ("tokens + prefix 32", lambda i, t: lure(i, t, chars=32)),
        ("tokens + prefix 48", lambda i, t: lure(i, t, chars=48)),
        ("tokens + prefix 80", lambda i, t: lure(i, t, chars=80)),
    ]
    for label, fn in variants:
        out = total(fn)
        print(f"{label:>34}{1 - out / raw:>11.1%}{(out - bare):>+10}")


def main(argv):
    paths = argv[1:] or sorted(glob.glob("data/samples/*.json"))
    if not paths:
        print("no payloads found — pass a path, or populate data/samples/", file=sys.stderr)
        return 2

    results = [measure(path) for path in paths]
    report(results)
    report_thresholds(results)
    report_encodings(results)
    report_lures(results)

    priced = [item for item in results if not item["reason"]]
    if priced:
        print(f"\nout of reach of a cell-level store: "
              f"{sum(item['wrap'] for item in priced)} tokens sitting in #wrap as raw JSON")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
