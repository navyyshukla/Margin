"""Does the MCP server keep the promises the MCP server makes?

The same argument as src/cli_test.py, one process boundary along. Every other
gate here calls Python functions; this one speaks JSON-RPC over a pipe to a
subprocess, because that is the only place the protocol exists. A server that
imports and returns the right string while failing to speak MCP is a server
nothing can call — and importing `fetch` directly would test everything except
the part nothing has tested (Rule 11).

Run: python src/mcp_test.py
"""

import json
import os
import subprocess
import sys
import tempfile

import store as store_module
from compress import compress_json
from render import STORE_PREFIX, parse
from tokens import token_count

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")

# sys.executable, not "<repo>/.venv/bin/python". The interpreter running this
# file already has everything the server needs, and hardcoding the venv path
# made this gate unrunnable exactly where it has to run: mutation_test.py copies
# the staged tree into a scratch directory, which by construction has no .venv,
# so the gate returned 2 and turned the whole mutation baseline red. Rule 12 —
# the environment a check runs in has to be the environment it assumes.
PYTHON = sys.executable

PROTOCOL = "2025-06-18"

_failures = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(f"{name}{': ' + detail if detail else ''}")
    return ok


class Server:
    """The server as a subprocess, spoken to the way a client would."""

    def __init__(self, store_root):
        env = dict(os.environ, MARGIN_STORE=store_root)
        self.process = subprocess.Popen(
            [PYTHON, SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, bufsize=1,
        )
        self._id = 0

    def request(self, method, params=None):
        self._id += 1
        message = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"server closed the pipe during {method}; "
                    f"stderr: {self.process.stderr.read()[:800]}"
                )
            reply = json.loads(line)
            # Notifications and unrelated traffic are skipped rather than
            # assumed absent: matching on the id is the only thing that makes
            # this a reply to *this* request.
            if reply.get("id") == self._id:
                return reply

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def initialize(self):
        reply = self.request("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "mcp_test", "version": "1"},
        })
        self.notify("notifications/initialized")
        return reply

    def call(self, tool, arguments):
        return self.request("tools/call", {"name": tool, "arguments": arguments})

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


def text_of(reply):
    """The text a tool call returned, or "" — never a raise on shape."""
    result = reply.get("result") or {}
    return "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict)
    )


# One needle, buried once in a long body — the realistic shape, and the only one
# where a query can beat fetching the whole value. The first fixture repeated its
# search term fourteen times in a short body, so the honest answer was "here is
# the whole thing" and the check demanding a *smaller* result failed against
# correct behaviour. A search fixture has to contain something worth searching
# for.
_FILLER = "Some ordinary narrative text about an unrelated part of the system. " * 14
BODY_A = f"{_FILLER}Login fails when the session cookie is rotated mid-request.{_FILLER}"
BODY_B = ("Deadlock in the scheduler when two workers claim the same lease. " * 28)


def build_document(store_root):
    """A real stored document, made the way `margin --store` makes one."""
    # The filler bodies are large on purpose: the truncation check needs a batch
    # that actually exceeds MAX_RESPONSE_TOKENS, and the first version's 150-token
    # fillers meant seven of them came to a tenth of the cap. The check was named
    # for a limit it never reached — Rule 6's corollary, and the same fixture
    # drift that made two closed-pipe checks vacuous in cli_test.py. The premise
    # is asserted below rather than trusted to stay true.
    payload = [
        {"i": 0, "body": BODY_A},
        {"i": 1, "body": BODY_B},
        *({"i": n, "body": f"filler body number {n} words words words " * 260}
          for n in range(2, 8)),
    ]
    backing = store_module.FileStore(store_root)
    text, _ = compress_json(payload, store=backing)
    if STORE_PREFIX not in text:
        raise RuntimeError("fixture did not store anything — the checks would be vacuous")
    return text, parse(text)["store"]


def main():
    print("MCP contract\n")
    with tempfile.TemporaryDirectory() as root:
        document, doc_id = build_document(root)
        # The premise, asserted rather than assumed: if the fixture stopped
        # storing, every check below would pass against an empty index.
        check("fixture: the document really carries handles",
              STORE_PREFIX in document and doc_id)

        server = Server(root)
        try:
            reply = server.initialize()
            check("initialize: the server answers and names itself",
                  (reply.get("result") or {}).get("serverInfo", {}).get("name") == "margin",
                  json.dumps(reply)[:300])

            listed = server.request("tools/list")
            tools = {t["name"] for t in (listed.get("result") or {}).get("tools", [])}
            check("tools/list: fetch is offered", "fetch" in tools, str(tools))

            body = text_of(server.call("fetch", {"document": doc_id, "ids": ["0001"]}))
            check("fetch: one id returns its stored content",
                  BODY_A[:60] in body, body[:200])

            both = text_of(server.call("fetch", {"document": doc_id, "ids": ["0001", "0002"]}))
            check("fetch: a batch returns every id in one call",
                  BODY_A[:60] in both and BODY_B[:60] in both, both[:200])

            # Rule 1's shape: a batch that quietly returns only the first item
            # still "works". Assert both are there AND that it beat one call.
            check("fetch: a batch is not just the first id",
                  len(both) > len(body), f"batch={len(both)} single={len(body)}")

            loose = text_of(server.call("fetch", {"document": doc_id, "ids": ["\\@0001[142t]", "2"]}))
            check("fetch: ids are accepted as written in the document",
                  BODY_A[:60] in loose and BODY_B[:60] in loose, loose[:200])

            hit = text_of(server.call(
                "fetch", {"document": doc_id, "ids": ["0001"], "query": "session cookie"}))
            check("query: returns the matching span, not the whole value",
                  "session cookie" in hit and len(hit) < len(body), f"{len(hit)} vs {len(body)}")

            miss = text_of(server.call(
                "fetch", {"document": doc_id, "ids": ["0001"], "query": "quantum entanglement"}))
            check("query: a miss says so rather than returning nothing",
                  "no match" in miss.lower(), miss[:200])

            unknown = text_of(server.call("fetch", {"document": doc_id, "ids": ["9999"]}))
            check("a handle not in the index says so", "not in" in unknown.lower(), unknown[:200])

            wrong = text_of(server.call("fetch", {"document": "deadbeefdeadbeefdeadbeef",
                                                  "ids": ["0001"]}))
            check("an unknown document says so rather than answering emptily",
                  "no store index" in wrong.lower(), wrong[:200])

            # Delete an object behind a live handle: the dangling case. An empty
            # string here would be answered as though the value were empty,
            # which is the silent-partial-output failure the whole design is
            # built to refuse.
            index = store_module.FileStore(root).read_index(doc_id)
            os.unlink(os.path.join(root, "objects", index["0001"]))
            gone = text_of(server.call("fetch", {"document": doc_id, "ids": ["0001"]}))
            check("a missing object is reported as MISSING, not as empty",
                  "missing" in gone.lower() and BODY_A[:60] not in gone, gone[:200])

            # The premise first: this batch has to exceed the cap, or the check
            # below is named for a limit it never reaches. Asserted against the
            # server's own constant rather than eyeballed.
            from mcp_server import MAX_RESPONSE_TOKENS
            index_now = store_module.FileStore(root).read_index(doc_id)
            batch = [f"{n:04d}" for n in range(2, len(index_now) + 1)]
            weight = sum(token_count(store_module.FileStore(root).get(index_now[i]))
                         for i in batch)
            check("fixture: the over-cap batch really exceeds the cap",
                  weight > MAX_RESPONSE_TOKENS, f"{weight} vs {MAX_RESPONSE_TOKENS}")

            big = text_of(server.call("fetch", {"document": doc_id, "ids": batch}))
            check("an over-cap batch truncates and says which ids it dropped",
                  "TRUNCATED" in big, big[-200:])
        finally:
            server.close()

    print()
    if _failures:
        print(f"{len(_failures)} MCP check(s) FAILED:")
        for line in _failures:
            print(f"  - {line}")
        return 1
    print("all MCP checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
