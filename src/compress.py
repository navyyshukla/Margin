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

Usage: python src/compress.py data/samples/some_response.json > compressed.json
"""

import json
import sys

import tiktoken

import render
from decompress import decompress
from detect import detect_content_type
from table import build_table, find_record_array

NOISE_KEYS = {"node_id", "gravatar_id"}


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


MIN_ROWS_TO_TABULATE = 2
# Measured 2026-09-07 on github_issues.json: the table format's fixed cost is
# one header line, so it starts paying at the first repeated row. Below this a
# table cannot win and is not worth attempting.

MIN_TABLE_SAVING = 0.10
# Measured 2026-09-07: the table saves 24.5% over stripped JSON on
# github_issues.json. Gated well below that so a differently-shaped payload
# still gets the win, while a payload where the table barely helps falls back
# to JSON rather than paying the risk of a second format for nothing.
# Deliberately NOT Headroom's 0.30 — that would reject our own best result.


def compress_json(data):
    """Full pipeline. Returns (text, notes).

    notes explains any fallback to plain JSON. It is not decoration: if a bug
    makes the table unusable and we silently emit JSON instead, every eval
    question still passes — the answers are all still there — and a broken
    format would ship looking green. The harness fails on a round-trip note for
    exactly that reason.
    """
    stripped = strip_boilerplate(data)
    as_json = json.dumps(stripped)

    rows, array_path = find_record_array(stripped)
    if rows is None:
        return as_json, ["no record array found — nothing to tabulate"]
    if len(rows) < MIN_ROWS_TO_TABULATE:
        return as_json, [f"only {len(rows)} row(s) — below MIN_ROWS_TO_TABULATE"]

    wrapper = {}
    if array_path:
        wrapper = {k: v for k, v in stripped.items() if k != array_path[0]}

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
        text = render.render(build_table(rows, array_path, wrapper))
        restored = decompress(text)
    except Exception as exc:
        return as_json, [f"table render/parse failed ({type(exc).__name__}: {exc})"]

    if restored != stripped:
        return as_json, ["ROUND-TRIP MISMATCH — fell back to JSON"]

    saving = 1 - token_count(text) / token_count(as_json)
    if saving < MIN_TABLE_SAVING:
        return as_json, [f"table saved only {saving:.1%} — below MIN_TABLE_SAVING"]

    return text, []


def token_count(text):
    """Tokens under cl100k_base — the unit every threshold here is measured in."""
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return len(_ENCODING.encode(text))


_ENCODING = None  # loaded once, lazily: get_encoding is slow to call repeatedly


def main():
    if len(sys.argv) != 2:
        print("usage: python src/compress.py <path-to-json-file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        raw_text = f.read()

    content_type, data = detect_content_type(raw_text)

    if content_type == "json":
        text, notes = compress_json(data)
        for note in notes:
            print(f"note: {note}", file=sys.stderr)
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        # No plain_text compression rule exists yet (nothing built it needs to
        # justify studying/writing one — see CLAUDE.md's "study just in time").
        # Pass it through unchanged rather than mangling content we don't
        # understand yet. sys.stdout.write, not print: print() would append a
        # newline the input never had, so the "unchanged" path wouldn't be
        # byte-for-byte unchanged.
        sys.stdout.write(raw_text)


if __name__ == "__main__":
    main()
