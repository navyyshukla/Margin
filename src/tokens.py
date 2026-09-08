"""Counting tokens, in one place.

This lived in compress.py, which was fine while only compress.py and its callers
needed it. Then table.py needed it too — deciding whether a dictionary column is
worth building is a comparison of token counts, and CLAUDE.md's standing rule is
that a threshold is measured rather than guessed, so a character-count proxy
would not do.

compress.py imports table.py, so table.py cannot import compress.py. Rather than
have table.py estimate tokens its own way — two token counts in one pipeline
that could disagree, which is Rule 4's shape — the function moved here and both
import it. `from compress import token_count` still works, so nothing that used
it before had to change.
"""

import functools

import tiktoken


@functools.lru_cache(maxsize=8)
def token_count(text):
    """Tokens under cl100k_base — the unit every threshold here is measured in.

    Cached because the same few strings get counted repeatedly: compress_json
    counts the compact JSON and the original, then the CLI's summary line wants
    exactly those two again. Encoding the largest sample costs 16 ms
    (github_issues.json, 50,031 tokens; the whole compression is 42 ms), so the
    repeats were not free. Safe to cache — a pure function of its argument.

    maxsize is small on purpose: the keys are whole payloads, and property_test
    counts thousands of generated ones. 8 covers the handful any single
    compression revisits and lets the rest fall out.
    """
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return len(_ENCODING.encode(text))


_ENCODING = None  # loaded once, lazily: get_encoding is slow to call repeatedly
