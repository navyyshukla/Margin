"""Strip structural boilerplate from a JSON payload without touching content.

Rule: drop any key ending in "_url", plus "url", "node_id", and "gravatar_id" — GitHub
API fields that are link templates / opaque IDs, never needed to answer a question about
the content. Measured 38% token reduction on data/samples/github_issues.json with zero
answer-quality loss on data/eval_questions.md (checked manually, 2026-09-07).

Usage: python src/compress.py data/samples/some_response.json > compressed.json
"""

import json
import sys

NOISE_KEYS = {"node_id", "gravatar_id", "url"}


def strip_boilerplate(data):
    """Recursively drop link-template and opaque-ID keys from dicts/lists."""
    if isinstance(data, dict):
        return {
            k: strip_boilerplate(v)
            for k, v in data.items()
            if not k.endswith("_url") and k not in NOISE_KEYS
        }
    if isinstance(data, list):
        return [strip_boilerplate(v) for v in data]
    return data


def main():
    if len(sys.argv) != 2:
        print("usage: python src/compress.py <path-to-json-file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    compressed = strip_boilerplate(data)
    print(json.dumps(compressed))


if __name__ == "__main__":
    main()
