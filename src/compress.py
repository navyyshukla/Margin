"""Strip structural boilerplate from a JSON payload without touching content.

Rules: drop any key ending in "_url", plus "node_id" and "gravatar_id" — link
templates and opaque IDs that no question about the content ever needs. A bare
"url" key is dropped only when the same object also carries "*_url" siblings,
because that pattern marks it as one more link template; on its own, "url" is
usually the record's actual subject (see _has_url_template_siblings).

Measured 38% token reduction on data/samples/github_issues.json with zero
answer-quality loss on data/eval_questions.md (2026-09-07).

This stage is deliberately LOSSY: the dropped fields are the product, not an
accident, and nothing restores them. Every later stage is exactly reversible.
That boundary is what round-trip verification is measured against — see
src/table.py.

Usage: margin data/samples/some_response.json > compressed.txt
       (this file is the library; src/cli.py is the program — see docs/cli.md)
"""

import json
import sys

import render
import store as store_module
from decompress import decompress
from tokens import token_count
from table import MIN_ROWS_TO_TABULATE, build_table, find_record_array, same_json

NOISE_KEYS = {"node_id", "gravatar_id"}

COMPACT = (",", ":")
# json.dumps defaults to ", " and ": " — whitespace that was never in the file
# as fetched. It matters twice, and both ways it flattered us:
#
#   1. As OUTPUT. On payloads with no record array the compressor emits this
#      JSON, and the padding made three of the six sample payloads come out
#      BIGGER than they went in — Open-Meteo by 19%, exchange rates by 25%.
#      A compressor that inflates a payload is worse than no compressor.
#   2. As the DENOMINATOR of the savings gate. Every "table beats JSON by N%"
#      figure was measured against an inflated baseline, so the gate was
#      grading against a competitor nobody would have shipped.
#
# docs/thresholds.md flagged this as a reporting caveat on 2026-09-08 and left
# the code alone. That was the wrong call: it is not a reporting problem, it is
# padding in the output. Compact everywhere, and the thresholds re-derived.


def strip_boilerplate(data, drop_bare_url=None):
    """Recursively drop link-template and opaque-ID keys from dicts/lists.

    drop_bare_url is decided once for the whole document and passed down; see
    uses_url_template_convention for why it is not decided per object.
    """
    if drop_bare_url is None:
        drop_bare_url = uses_url_template_convention(data)

    if isinstance(data, dict):
        return {
            k: strip_boilerplate(v, drop_bare_url)
            for k, v in data.items()
            if not k.endswith("_url")
            and k not in NOISE_KEYS
            and not (k == "url" and drop_bare_url)
        }
    if isinstance(data, list):
        return [strip_boilerplate(v, drop_bare_url) for v in data]
    return data


def uses_url_template_convention(data):
    """Does this document hang "*_url" link templates off its objects anywhere?

    Decides whether a bare "url" key is boilerplate or content — and it is a
    property of the whole document, not of one object. GitHub hangs a family of
    link templates off its records (comments_url, events_url, labels_url, ...),
    which marks the bare "url" as one more of them. But its nested label and
    reaction objects carry a bare "url" with no "*_url" siblings of their own,
    so deciding per object would keep those (739 wasted tokens in labels alone,
    measured 2026-09-07) while correctly dropping the ones at the top.

    Meanwhile an API that never uses the convention — a HackerNews story, where
    "url" IS the story — has no "*_url" key anywhere, so its content survives.

    One convention, decided once, applied throughout.
    """
    if isinstance(data, dict):
        if any(k.endswith("_url") for k in data):
            return True
        return any(uses_url_template_convention(v) for v in data.values())
    if isinstance(data, list):
        return any(uses_url_template_convention(v) for v in data)
    return False


def skeleton(data, array_path):
    """The whole document with the record array lifted out of it.

    This is what the `#wrap` line carries, and it used to be something narrower:
    the array's top-level *siblings*. That was only ever correct because the
    records were always at depth 1. Once find_record_array could return
    ["data", "items"], siblings-of-the-first-key was wrong in both directions —
    it dropped everything beside `items` inside `data`, and decompress._nest
    merging `{"data": {"items": rows}}` over the wrapper at the top level
    overwrote the rest of `data` wholesale.

    Carrying the entire skeleton instead makes the depth irrelevant: whatever
    the document held, minus the rows, is written down, and the rows go back
    exactly where they came from.

    Costs nothing at depth 1 — for {"hits": [...], "nbHits": 431} this returns
    {"nbHits": 431}, the same dict the old code built — so HackerNews and GitHub
    render byte-identically to before.
    """
    if not array_path:
        return {}  # the records were the whole document; there is no wrapper

    key = array_path[0]
    if len(array_path) == 1:
        return {k: v for k, v in data.items() if k != key}
    return {**data, key: skeleton(data[key], array_path[1:])}


MIN_TABLE_SAVING = 0.05
# Re-derived 2026-09-08 across eight payloads, against the COMPACT JSON the
# compressor would actually emit instead (see COMPACT — the old 0.10 was set
# against a padded baseline, so every figure behind it was inflated).
#
# Real savings, table vs. the JSON that is its actual alternative:
#
#   hn_stories          43.0%      openmeteo         no table
#   graphql_countries   25.1%      coingecko         no table
#   github_issues       18.9%      exchangerates     no table
#   jsonplaceholder     10.5%
#   pokeapi_ditto        6.2%
#
# 0.10 rejected pokeapi_ditto's correct, verified 6.2% — 490 tokens thrown away
# to avoid "the risk of a second format". That risk was priced when the format
# was unproven; it is now self-describing (#legend) and has passed a cold read
# by a model with no access to this repo, so the price has dropped and the gate
# should follow. 0.05 keeps every real win in the sample set with room beneath
# the smallest, and still refuses a table that merely breaks even.
#
# Deliberately NOT Headroom's 0.30, which would reject all but one of these.


def compress_json(data, original_text=None, store=None):
    """Full pipeline. Returns (text, notes).

    `store`, when given, is where bulk cells go instead of into the document —
    each becomes a `\\@nnnn` handle and the content is written to the store. When
    it is None nothing about this function changes, which is deliberate: every
    gate that existed before the store still exercises the storeless path, and a
    payload with no cell worth storing produces a byte-identical document either
    way.

    original_text, when given, is the payload exactly as it arrived. It is used
    for one thing: making sure the output is never longer than the input. A
    file's own formatting can tokenize marginally better than any canonical
    re-serialisation of it, so "we could not improve this" must mean handing
    back what we were given, not a re-encoded version of the same data that
    happens to cost four tokens more.

    notes explains any fallback to plain JSON. It is not decoration: if a bug
    makes the table unusable and we silently emit JSON instead, every eval
    question still passes — the answers are all still there — and a broken
    format would ship looking green. The harness fails on a round-trip note for
    exactly that reason.
    """
    stripped = strip_boilerplate(data)
    as_json = json.dumps(stripped, separators=COMPACT)

    def result(text, notes):
        """Never hand back something longer than what we were given."""
        if original_text is not None and token_count(text) >= token_count(original_text):
            return original_text, notes + ["no improvement — returned the input unchanged"]
        return text, notes

    rows, array_path, key_column = find_record_array(stripped)
    if rows is None:
        return result(as_json, ["no record array found — nothing to tabulate"])
    if len(rows) < MIN_ROWS_TO_TABULATE:
        return result(as_json, [f"only {len(rows)} row(s) — below MIN_ROWS_TO_TABULATE"])

    wrapper = skeleton(stripped, array_path)

    # Don't reason about whether the inverse is correct — run it. A transform we
    # cannot undo is a transform we don't ship.
    #
    # Rendering or re-reading can also raise outright: a key containing a comma
    # or a newline collides with the header's own syntax, since the header is
    # one line of comma-separated column specs. Catching that here rather than
    # forbidding such keys keeps one rule — "if the table cannot be proved
    # correct, emit JSON" — instead of two, and means an unanticipated payload
    # degrades to plain JSON rather than crashing the compressor.
    try:
        table = build_table(rows, array_path, wrapper, key_column)

        pending = {}
        doc_id = None
        if store is not None:
            pending = store_module.stash_bulk_cells(table)
            # Only when something was actually stored. A document that stores
            # nothing must not carry a #store line pointing at an empty index —
            # it would be a claim with nothing behind it, and it would make the
            # storeless and stored paths produce different bytes for payloads
            # where the store does nothing (five of the eight samples).
            if pending:
                doc_id = store_module.document_id(as_json)
                table["store"] = doc_id

        text = render.render(table)
        # Verified against staged content, never against the real store: every
        # check below can still abandon this document, and content written for a
        # document nobody emits is an orphan nothing will ever collect.
        restored = decompress(
            text, store_module.staged(doc_id, pending) if pending else store
        )
    except Exception as exc:
        return result(as_json, [f"table render/parse failed ({type(exc).__name__}: {exc})"])

    # same_json, not ==. Python's == says True == 1 and 0 == 0.0, so a document
    # that decoded ints as floats compared equal and shipped. Found by review
    # 2026-09-08 on a column holding 0 in some rows and 0.0 in others.
    if not same_json(restored, stripped):
        return result(as_json, ["ROUND-TRIP MISMATCH — fell back to JSON"])

    saving = 1 - token_count(text) / token_count(as_json)
    if saving < MIN_TABLE_SAVING:
        return result(as_json, [f"table saved only {saving:.1%} — below MIN_TABLE_SAVING"])

    final, notes = result(text, [])

    # The only place anything is written to the real store, and it is after every
    # path that can still discard this document — including result(), which hands
    # back the original when the table did not actually beat it. `is`, not `==`:
    # the question is whether this exact document is the one being returned.
    if pending and final is text:
        store_module.commit(store, doc_id, pending)
    return final, notes


if __name__ == "__main__":
    # One input path for the whole project, and it lives in cli.py. This file
    # used to read argv and stdout itself, which meant `margin` and
    # `python src/compress.py` were two implementations of "read a payload,
    # write a document" that could drift — the same mistake Rule 4 records for
    # encode_cell and decode_cell, one layer up.
    #
    # runpy, not `import cli; cli.main()`, and the difference is the point. The
    # first version called main() but not die_on_broken_pipe(), so
    # `python src/compress.py f.json | head -1` still printed the exact
    # traceback the change was written to remove — a second entry point is a
    # second place to forget a line, which is the whole failure being fixed.
    # Running cli.py *as __main__* means it does everything it does for
    # `margin`, and there is no list of setup steps to keep in sync.
    #
    # It does NOT stop this file being loaded twice — an earlier version of this
    # comment claimed it did, and `python -X importtime src/compress.py` says
    # otherwise: this file stays registered as `__main__`, so cli.py's
    # `from compress import ...` executes the body again under the name
    # `compress`, giving two `_ENCODING` globals and two token_count caches.
    # Only the `compress` copy is ever used, so it costs one extra import and
    # nothing else. Recorded rather than quietly dropped, because a false
    # invariant in a comment is worse than an unstated one (Rule 5).
    import os
    import runpy

    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py"),
                   run_name="__main__")
