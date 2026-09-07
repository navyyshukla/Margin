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


def strip_boilerplate(data):
    """Recursively drop link-template and opaque-ID keys from dicts/lists."""
    if isinstance(data, dict):
        drop_bare_url = _has_url_template_siblings(data)
        return {
            k: strip_boilerplate(v)
            for k, v in data.items()
            if not k.endswith("_url")
            and k not in NOISE_KEYS
            and not (k == "url" and drop_bare_url)
        }
    if isinstance(data, list):
        return [strip_boilerplate(v) for v in data]
    return data


def _has_url_template_siblings(obj):
    """Does this object carry GitHub-style "*_url" link templates?

    Decides whether a bare "url" key on THIS object is boilerplate or content.
    GitHub hangs a whole family of link templates off every object
    (comments_url, events_url, labels_url, ...), and the bare "url" is just one
    more of them. But plenty of APIs use "url" for the thing the record is
    actually about — a HackerNews story's url IS the story — and deleting that
    would throw away the answer, not the noise.

    Keying off the sibling pattern rather than the name means the rule is about
    structure, which travels to APIs we have not seen. Judging by name alone
    does not.
    """
    return any(k.endswith("_url") for k in obj)


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
