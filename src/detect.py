"""Content-type detector — Week 2 of the build plan, done last (out of order).

The point: before you can compress something, you need to know WHAT it is. A JSON
API response and a wall of plain English need different rules. This starts as an
ordered cascade of cheap checks — try the cheapest/most certain test first, fall
through to the next if it doesn't match.

Only two types for now (json, plain_text) per the plan. Add a new type only once
there's an actual sample in data/samples/ that isn't one of these — no
speculative types.

Detection returns the parsed value alongside the type, so callers don't have to
parse the same text a second time to use it.

Usage: python src/detect.py data/samples/some_file.json
"""

import json
import sys


def detect_content_type(raw_text: str) -> tuple[str, object]:
    """Classify raw_text and hand back whatever parsing already produced.

    Returns ("json", parsed_value) or ("plain_text", None).
    """
    try:
        return "json", json.loads(raw_text)
    except json.JSONDecodeError:
        return "plain_text", None


def main():
    if len(sys.argv) != 2:
        print("usage: python src/detect.py <path-to-file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        raw_text = f.read()

    content_type, _parsed = detect_content_type(raw_text)
    print(content_type)


if __name__ == "__main__":
    main()
