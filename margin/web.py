"""margin web — the one page that shows what a payload saved.

The CLI prints a percentage on stderr, which is the right output for a pipe and
a poor way to see what this does. This serves one page: paste JSON, see the
document and the saving, copy it.

It calls compress_json — the same function cli.py calls, not a copy of it. That
is the whole design of this file: there is exactly one compressor, so the gates
that guard it guard this too, and a change to the pipeline reaches the page for
free. A JavaScript reimplementation in the browser was considered and rejected
for the opposite reason — a second implementation no gate here can see, free to
drift, which is what CLAUDE.md's scope rule exists to prevent.

127.0.0.1 only, and there is deliberately no --host flag. 0.0.0.0 would put an
unauthenticated endpoint that parses untrusted JSON onto the local network, and
docs/cli.md's standing pattern is to decline flags that buy keystrokes.

The passthrough policy here is NOT a second policy: not JSON, not UTF-8 and
too-deep-to-parse all behave the way cli.py settled them — the bytes come back,
a note says why, and the status is a success. src/web_test.py asserts the
agreement rather than re-asserting the behaviour.
"""

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .compress import compress_json
from .detect import detect_content_type
from .tokens import token_count

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "index.html")

DEFAULT_PORT = 8731
# Arbitrary, and said so rather than implied: this is not a measured number. It
# is unassigned in the IANA registry and stable enough to bookmark. A port
# already in use falls back to an ephemeral one rather than failing, because a
# second `margin web` is a thing people do and not an error.

MAX_BODY_BYTES = 64 * 1024 * 1024
# A bound, not a measurement, and the number is not the point: an unbounded read
# of Content-Length is an out-of-memory a browser tab can trigger. 64 MiB is
# about 300x the largest sample payload (github_issues.json, 50,031 tokens,
# ~210 KB on disk), so it bounds the damage without being a limit anyone meets
# by accident. src/web_test.py moves it rather than posting 64 MiB to prove it.


def compress_payload(raw):
    """The CLI's pipeline, as a dict for the page. Never raises.

    Mirrors cli.main()'s order exactly — decode, parse, type-check, compress —
    because the two agreeing is the property src/web_test.py checks. Where the
    CLI writes a note to stderr and the bytes to stdout, this puts both in one
    response.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # The one place this cannot match the CLI, and it is a property of JSON
        # rather than a decision: the CLI hands back the original bytes, and a
        # JSON response has nowhere to put bytes that are not text. So it says
        # what happened and shows what could be decoded, and the note is the
        # part that matters.
        return {"document": raw.decode("utf-8", "replace"),
                "before": 0, "after": 0, "percent": 0.0,
                "note": "not UTF-8 — passed through unchanged", "dropped": []}

    def unchanged(note):
        return {"document": text, "before": 0, "after": 0, "percent": 0.0,
                "note": note, "dropped": []}

    # Two guards, because docs/cli.md records that there are two recursions and
    # a single payload can only ever reach one of them: json.loads raises
    # RecursionError itself past roughly 3x the limit, from inside
    # detect_content_type, which catches only JSONDecodeError; below that the
    # parse succeeds and the pipeline blows up further in. `except Exception`,
    # not RecursionError — the promise is about the pipeline, not about which
    # bugs were anticipated.
    try:
        content_type, data = detect_content_type(text)
    except Exception as exc:                           # noqa: BLE001
        return unchanged(f"could not parse ({type(exc).__name__}: {exc})"
                         f" — passed through unchanged")
    if content_type != "json":
        return unchanged("not JSON — passed through unchanged")

    try:
        document, notes = compress_json(data, original_text=text)
    except Exception as exc:                           # noqa: BLE001
        return unchanged(f"could not compress ({type(exc).__name__}: {exc})"
                         f" — passed through unchanged")

    before, after = token_count(text), token_count(document)
    dropped = []
    for note in notes:
        if note.startswith("dropped "):
            # The note's own shape: "dropped N key(s) not recoverable: a, b".
            # Parsed rather than recomputed, so the page cannot disagree with
            # what the compressor said it did.
            dropped = note.split(": ", 1)[1].split(", ")
    return {
        "document": document,
        "before": before,
        "after": after,
        # max(), because compress_json already guarantees the output is never
        # longer than the input: a negative here would be a bug upstream, and
        # this makes it show up as 0.0% rather than as a bar running backwards.
        "percent": round(max(0.0, (1 - after / before) * 100), 1),
        "note": next((n for n in notes if not n.startswith("dropped ")), None),
        "dropped": dropped,
    }


class Handler(BaseHTTPRequestHandler):
    """Two routes. Anything else is a 404."""

    # Silent by default: the page posts on every pause in typing, and a request
    # log under the URL would bury the one line that matters.
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page is served by this process and talks to nothing else, so
        # nothing should be sniffing a type out of the body.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/":
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        # A missing page file is a broken install, not a bug in the request, and
        # it has to SAY so. Letting the open() raise kills the connection with a
        # traceback in the terminal the user is watching for the URL, and the
        # browser shows "connection reset" — which names neither the file nor
        # the cause. src/package_test.py exists to stop a wheel shipping without
        # this file; if one ever does, the person running it should be told
        # which file is missing.
        try:
            with open(PAGE, "rb") as f:
                page = f.read()
        except OSError as exc:
            return self._send(500, f"margin: cannot read {PAGE}: {exc.strerror}\n"
                                   f"this install is missing its page file\n"
                                   .encode(), "text/plain; charset=utf-8")
        self._send(200, page, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/compress":
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        # A missing or unparseable Content-Length is a 400 rather than a
        # traceback: curl, a port scanner and a half-closed browser connection
        # all produce one, and a traceback here lands in the terminal the user
        # is watching for the URL.
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return self._send(400, b'{"error":"empty body"}',
                              "application/json; charset=utf-8")
        if length > MAX_BODY_BYTES:
            return self._send(413, b'{"error":"payload too large"}',
                              "application/json; charset=utf-8")
        body = json.dumps(compress_payload(self.rfile.read(length))).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")


def bind(port=DEFAULT_PORT):
    """A server on 127.0.0.1, falling back to an ephemeral port if `port` is taken.

    Returns the server rather than serving it: src/web_test.py needs to run it
    on a thread and read back the port it actually got.
    """
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # Almost always a second `margin web` already running. Take any free
        # port and let main() print the real URL.
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main():
    server = bind()
    url = f"http://127.0.0.1:{server.server_port}"
    # stderr, because every note this project prints goes to stderr and this one
    # is not a document. Printed BEFORE opening the browser: if the browser call
    # fails, or opens nothing, the URL is still on screen to click.
    print(f"margin: serving {url} — Ctrl-C to stop", file=sys.stderr)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        server.shutdown()
        server.server_close()
