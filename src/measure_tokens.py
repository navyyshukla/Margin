"""Show where tokens actually go in a real JSON payload.

Usage: python src/measure_tokens.py data/samples/some_response.json
"""

import json
import sys

import tiktoken

STRUCTURAL_CHARS = set('{}[]",:')


def count_tokens(text: str, encoding) -> int:
    return len(encoding.encode(text))


def structural_vs_value_tokens(data, encoding) -> tuple[int, int]:
    """Split token count into JSON punctuation/keys vs. actual values.

    Renders the payload twice: once as-is, once with only the values
    (no keys, no brackets) so the difference shows the pure structural cost.
    """
    full_text = json.dumps(data)
    full_tokens = count_tokens(full_text, encoding)

    values_only = _extract_values(data)
    values_text = " ".join(str(v) for v in values_only)
    value_tokens = count_tokens(values_text, encoding)

    structural_tokens = full_tokens - value_tokens
    return structural_tokens, value_tokens


def _extract_values(data):
    """Walk a JSON structure and collect every leaf value."""
    values = []
    if isinstance(data, dict):
        for v in data.values():
            values.extend(_extract_values(v))
    elif isinstance(data, list):
        for item in data:
            values.extend(_extract_values(item))
    else:
        values.append(data)
    return values


def main():
    if len(sys.argv) != 2:
        print("usage: python src/measure_tokens.py <path-to-json-file>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path) as f:
        raw_text = f.read()
        data = json.loads(raw_text)

    encoding = tiktoken.get_encoding("cl100k_base")

    total_tokens = count_tokens(raw_text, encoding)
    structural_tokens, value_tokens = structural_vs_value_tokens(data, encoding)

    print(f"file: {path}")
    print(f"total tokens: {total_tokens}")
    print(f"  structural (keys, brackets, punctuation): {structural_tokens} "
          f"({structural_tokens / total_tokens:.0%})")
    print(f"  value content: {value_tokens} ({value_tokens / total_tokens:.0%})")

    if isinstance(data, list):
        print(f"array length: {len(data)} items")
        if data:
            per_item = total_tokens / len(data)
            print(f"~{per_item:.1f} tokens/item — repeated keys are the tax you're paying per row")


if __name__ == "__main__":
    main()
