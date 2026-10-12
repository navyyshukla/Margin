"""Does the page keep the CLI's promises?

The UI calls compress_json — the same function cli.py calls — so this gate does
not re-ask whether compression works. property_test.py owns that. It asks the
questions that only exist at the HTTP boundary, which is the fourth process
boundary in this repo and Rule 11 one more time: argv, streams and exit codes
only exist at a process boundary, and a request body, a status code and a
Content-Length only exist at this one.

cli.py has already settled what happens to input that is not JSON, not UTF-8, or
too deep to parse. This gate's real job is making sure the page AGREES with it
rather than inventing a second policy — including that the page's document is
byte-identical to the library's, which is the only thing standing between one
compressor and two.

Run: python src/web_test.py
"""

import json
import sys
import threading
import urllib.error
import urllib.request

from margin import web
from margin.compress import compress_json
from margin.tokens import token_count

failures = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(name)
    return ok


TABULATES = json.dumps([
    {"id": 1, "state": "open", "title": "first"},
    {"id": 2, "state": "open", "title": "second"},
    {"id": 3, "state": "open", "title": "third"},
])

WITH_LINKS = json.dumps([
    {"id": 1, "html_url": "http://x/1", "t": "aaaaaaaaaaaa"},
    {"id": 2, "html_url": "http://x/2", "t": "bbbbbbbbbbbb"},
    {"id": 3, "html_url": "http://x/3", "t": "cccccccccccc"},
])


def post(url, body, headers=None):
    """Returns (status, body). An HTTP error status is a result here, not an
    exception: 400 and 413 are two of the behaviours under test."""
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def get(url):
    """Returns (status, body). Same reasoning as post(): an error status is a
    result to check, not an exception to abort the gate on — and a gate that
    stops at its first failure hides every finding behind it."""
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def expected_after(payload):
    """What the library makes of this payload, counted the way the CLI counts
    it. If the page disagrees with this, there are two compressors."""
    text, _ = compress_json(json.loads(payload), original_text=payload)
    return token_count(text)


def main():
    print("web contract\n")
    server = web.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, page = get(base + "/")
        check("GET / serves the page", status == 200 and b"<title>" in page,
              f"status={status} {len(page)}B")
        # Nothing from the network. A CDN script tag or a Google Fonts link
        # would make the page silently worse offline, which for a tool that
        # runs on localhost is most of the time it will be used.
        check("the page loads nothing from the network",
              b"http://" not in page and b"https://" not in page,
              "an absolute URL in the page means it fetches something")

        status, body = post(base + "/compress", TABULATES.encode())
        got = json.loads(body)
        check("POST /compress compresses",
              status == 200 and got["document"].startswith("#margin/v1"),
              f"status={status} {str(got)[:200]}")
        check("it reports both counts and a percent",
              got["before"] > got["after"] > 0 and 0 < got["percent"] < 100,
              str(got)[:200])

        # The one check that keeps this from becoming a second implementation.
        text, _ = compress_json(json.loads(TABULATES), original_text=TABULATES)
        check("the page's document is byte-identical to the library's",
              got["document"] == text,
              "the UI has drifted from the compressor")

        # --- the CLI's settled passthrough policy, which the page must share --
        status, body = post(base + "/compress", b"<html>not json</html>")
        got = json.loads(body)
        check("not JSON: 200, bytes unchanged, and it says so",
              status == 200 and got["document"] == "<html>not json</html>"
              and "not JSON" in (got["note"] or ""),
              f"status={status} {str(got)[:200]}")

        status, body = post(base + "/compress", b"\xff\xfe\x00not utf-8")
        got = json.loads(body)
        check("not UTF-8: 200 and a note, not a 500",
              status == 200 and "UTF-8" in (got["note"] or ""),
              f"status={status} {body[:200]!r}")

        status, body = post(base + "/compress", b'{"a":1}')
        got = json.loads(body)
        check("nothing worth compressing: 0.0% is not an error",
              status == 200 and got["percent"] == 0.0, str(got)[:200])

        # Both recursions, because docs/cli.md records that there are two and a
        # single payload can only ever reach one of them: past roughly 3x the
        # limit the parse succeeds and the pipeline dies downstream; past 10x
        # json.loads raises RecursionError itself.
        for multiple, where in ((3, "downstream"), (10, "in the parser")):
            depth = sys.getrecursionlimit() * multiple
            deep = ("[" * depth) + ("]" * depth)
            status, body = post(base + "/compress", deep.encode())
            got = json.loads(body)
            check(f"{multiple}x the recursion limit ({where}): 200, unchanged, no traceback",
                  status == 200 and got["document"] == deep,
                  f"status={status} {body[:160]!r}")

        status, body = post(base + "/compress", b"")
        check("empty body: 400, because there is no argv to be wrong about",
              status == 400, f"status={status}")

        status, _ = get(base + "/nope")
        check("an unknown GET path 404s", status == 404, f"status={status}")

        status, _ = post(base + "/elsewhere", b"{}")
        check("an unknown POST path 404s", status == 404, f"status={status}")

        # The cap, with the cap moved rather than 64 MiB pushed over loopback.
        # The number is not what is under test — that it is enforced at all is,
        # and a 64 MiB POST to prove it would make this gate the slowest thing
        # in the harness for no extra information. Restored afterwards so no
        # later check runs against a 1 KiB limit.
        real_cap = web.MAX_BODY_BYTES
        try:
            web.MAX_BODY_BYTES = 1024
            status, body = post(base + "/compress", b"x" * 2048)
            check("a body over the cap: 413, not an unbounded read",
                  status == 413, f"status={status}")
        finally:
            web.MAX_BODY_BYTES = real_cap

        # Concurrency. ThreadingHTTPServer means compress_payload runs on
        # several threads at once over a shared token_count memo, so the
        # question is whether eight simultaneous requests all get the right
        # answer — not merely whether the server survives them.
        want = expected_after(TABULATES)
        results = []
        lock = threading.Lock()

        def hammer():
            _, b = post(base + "/compress", TABULATES.encode())
            with lock:
                results.append(json.loads(b)["after"])

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("8 concurrent requests all return the same correct count",
              len(results) == 8 and set(results) == {want},
              f"want {want}, got {sorted(results)}")

        # The honesty line's data. Rule 3's family again: the document is
        # identical whether or not this list is populated.
        status, body = post(base + "/compress", WITH_LINKS.encode())
        got = json.loads(body)
        check("it names the keys the lossy stage took",
              got["dropped"] == ["html_url"], f"dropped={got.get('dropped')}")

        status, body = post(base + "/compress", TABULATES.encode())
        got = json.loads(body)
        check("and says nothing when it took nothing",
              got["dropped"] == [], f"dropped={got.get('dropped')}")
    finally:
        server.shutdown()
        server.server_close()

    # A second bind on a port already in use must fall back, not raise. A
    # second `margin web` is a thing people do, not an error.
    taken = web.bind()
    try:
        # The OSError is caught HERE rather than left to propagate, and that is
        # not defensive habit. mutation_test.py demands that the check named for
        # a behaviour be the one that fails; a gate that dies on an exception
        # produces no failure line at all, so the mutation "a taken port raises
        # instead of falling back" came back SURVIVED while the bug was live.
        # docs/status.md records the same trap one stage earlier, in
        # detects_a_broken_store. An exception here IS the failure, so it is
        # reported as one.
        second = None
        try:
            second = web.bind(port=taken.server_port)
        except OSError as exc:
            check("a taken port falls back to another one", False,
                  f"bind raised instead of falling back: {exc}")
        if second is not None:
            try:
                check("a taken port falls back to another one",
                      second.server_port != taken.server_port,
                      f"both landed on {second.server_port}")
            finally:
                second.server_close()
    finally:
        taken.server_close()

    print()
    if failures:
        print(f"{len(failures)} web check(s) FAILED: {', '.join(failures)}")
        return 1
    print("all web checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
