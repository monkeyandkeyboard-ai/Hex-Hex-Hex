"""Tiny static file server for the client/ folder.
Runs alongside the GEP WebSocket server so the whole game is one
`python -m gep.server` command.
"""
import http.server
import pathlib
import threading

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
CLIENT_DIR = ROOT_DIR / "client"
HTTP_PORT = 8080


class Handler(http.server.SimpleHTTPRequestHandler):
    """Plain static handler -- everything the client needs, art included,
    lives under client/. Art deliberately sits at client/art rather than a
    repo-root ART/ reached by a path rewrite: any static server pointed at
    client/ then serves the whole game, so sprites do not silently 404 (and
    fall back to dots) just because the page was served off a different port.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def end_headers(self):
        # Dev server: never let the browser cache client code. Without this
        # there are no cache headers at all, so browsers apply heuristic
        # freshness and can serve a stale module after an edit -- which shows
        # up as a mismatched-import error between one fresh file and one
        # cached one, not as an obvious "you have old code" symptom.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise


def start_in_thread():
    # ThreadingHTTPServer: browsers fetch all ES modules in parallel, and a
    # single-threaded server refuses the overflow connections, which breaks
    # the whole module graph (page loads, scripts never run).
    server = http.server.ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server
