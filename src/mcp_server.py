"""The MCP server: what makes Margin a tool the model calls.

A `#margin/v1` document with a `#store` line has `\\@0001[142t]` where its bulk
values used to be. This serves them back. It is the other half of the store —
without it, `margin --store` produces a document nobody can fully read, which is
why the CLI keeps the store off by default.

This is the step that reverses CLAUDE.md's "no proxy server, no hosted API, not
yet", deliberately and with the reasoning in docs/status.md.

## Three decisions, taken from what retrieval tools get wrong

**Fetch takes a list.** Models resolve handles one at a time if you let them,
and every round trip re-serialises the whole conversation. Batching is the single
biggest saving available here and it costs nothing to offer.

**Fetch takes an optional `query`.** Most fetches want one paragraph of a
4,000-token body. Returning only the matching spans is the difference between
this tool saving context and spending it. Borrowed from Headroom, whose
`headroom_retrieve(hash, query?)` does exactly this — the best idea in their
codebase.

**Returns are capped and say when they are truncated.** A retrieval tool that
blows the context window it just saved is the classic failure of the genre. The
cap is generous, stated, and reported rather than silent, because a model that
does not know it got a partial answer will answer as though it got all of it.

Run:  python src/mcp_server.py
Wire: claude mcp add margin -- <repo>/.venv/bin/python <repo>/src/mcp_server.py
"""

import os
import sys

from mcp.server.mcpserver import MCPServer

import store as store_module
from tokens import token_count

# Roughly the size of a large issue body, times a few. Generous enough that a
# normal fetch is never truncated and small enough that a careless batch cannot
# spend the whole window. Stated in the response when it bites, never silent.
MAX_RESPONSE_TOKENS = 8000

# How much context to give either side of a query match. Two sentences is enough
# to tell whether a hit is the one you wanted without returning the paragraph.
QUERY_CONTEXT_CHARS = 240

server = MCPServer(
    name="margin",
    instructions=(
        "Resolves \\@nnnn handles in #margin/v1 documents. A document carrying a "
        "#store line has had its bulk values moved out; each \\@nnnn[Nt] marks a "
        "value that is NOT in the document, where N is what it costs to fetch. "
        "Pass the id from the #store line as `document` and the handle numbers as "
        "`ids`. Fetch several at once rather than one per turn, and pass `query` "
        "when you only need the part of a long value that mentions something."
    ),
)


def _open_store():
    """The store the CLI wrote to. One place, so the two cannot disagree."""
    return store_module.FileStore(store_module.default_root())


def _spans_matching(text, query):
    """The parts of `text` that mention `query`, with a little context.

    Case-insensitive and literal — not a regex, because a model writing a query
    is describing what it wants, not composing a pattern, and a stray `(` should
    not become a syntax error it has to debug.
    """
    haystack = text.lower()
    needle = query.lower()

    ranges = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found == -1:
            break
        left = max(0, found - QUERY_CONTEXT_CHARS)
        right = min(len(text), found + len(needle) + QUERY_CONTEXT_CHARS)
        # Merged as they are collected. Without this, a term that appears
        # fourteen times in one body returned fourteen overlapping windows —
        # 5,946 characters of "context" around an 847-character value, seven
        # times *more* than not querying at all. A search that returns more than
        # the thing being searched is worse than no search, and this is exactly
        # the shape of failure the whole tool exists to prevent.
        if ranges and left <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], right))
        else:
            ranges.append((left, right))
        start = found + len(needle)

    spans = [text[left:right] for left, right in ranges]

    # The backstop, because merging is not a guarantee: a term scattered evenly
    # through a long value produces many non-overlapping windows that still add
    # up to more than the value. If the answer is not smaller, hand back the
    # value itself — the caller asked to spend fewer tokens, not to receive the
    # same content in fragments.
    if sum(len(span) for span in spans) >= len(text):
        return [text]
    return spans


@server.tool(
    description=(
        "Fetch the values behind \\@nnnn handles in a #margin/v1 document. "
        "`document` is the id on the document's #store line; `ids` are the handle "
        "numbers, e.g. ['0001','0004']. Pass several at once. With `query`, only "
        "the matching parts of each value are returned."
    )
)
def fetch(document: str, ids: list[str], query: str | None = None) -> str:
    """Resolve handles to their stored content."""
    if not ids:
        return "No ids requested. Pass the numbers from the \\@nnnn handles you want."

    backing = _open_store()
    try:
        index = backing.read_index(document)
    except KeyError:
        return (
            f"No store index for document {document!r}. Check the id on the "
            f"#store line. This machine's store is {store_module.default_root()}."
        )

    parts = []
    spent = 0
    truncated = []

    for cell_id in ids:
        # Normalised, because a model reading `\@0001[142t]` may well pass
        # "0001", "1", or "\\@0001". Accepting all three costs three lines and
        # saves a confusing empty result.
        key = cell_id.strip().lstrip("\\@").split("[")[0].strip()
        key = key.zfill(store_module.ID_WIDTH) if key.isdigit() else key

        if key not in index:
            parts.append(f"[{cell_id}] not in this document's index")
            continue
        try:
            content = backing.get(index[key])
        except KeyError:
            # The dangling case. Said plainly rather than returned as an empty
            # string: a model given "" will answer as though the value were
            # empty, which is the silent-partial-output failure this whole
            # design is built to avoid.
            parts.append(f"[{key}] MISSING from the store — the content is gone, not empty")
            continue

        if query:
            spans = _spans_matching(content, query)
            if not spans:
                parts.append(f"[{key}] no match for {query!r} in this value "
                             f"({token_count(content)} tokens)")
                continue
            content = "\n…\n".join(spans)

        cost = token_count(content)
        if spent + cost > MAX_RESPONSE_TOKENS:
            truncated.append(key)
            continue
        spent += cost
        parts.append(f"[{key}]\n{content}")

    if truncated:
        parts.append(
            f"TRUNCATED: {', '.join(truncated)} not included — the response hit the "
            f"{MAX_RESPONSE_TOKENS}-token cap. Fetch them in a second call, or pass "
            f"`query` to get only the parts you need."
        )

    return "\n\n".join(parts)


def main():
    root = store_module.default_root()
    if not os.path.isdir(os.path.expanduser(root)):
        # Announced on stderr, not fatal: the store is created by the first
        # `margin --store` run, and a server that refused to start before then
        # would be unwireable until after it was needed.
        print(f"margin-mcp: no store at {root} yet — run `margin --store <file>` "
              f"to create one", file=sys.stderr)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
