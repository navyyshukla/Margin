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

from detect import detect_content_type

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


def main():
    if len(sys.argv) != 2:
        print("usage: python src/compress.py <path-to-json-file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        raw_text = f.read()

    content_type, data = detect_content_type(raw_text)

    if content_type == "json":
        compressed = strip_boilerplate(data)
        print(json.dumps(compressed))
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
