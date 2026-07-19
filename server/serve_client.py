"""Tiny static file server for the client/ folder.
Runs alongside the GEP WebSocket server so the whole game is one
`python -m gep.server` command.
"""
import http.server
import pathlib
import threading
import urllib.parse

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
CLIENT_DIR = ROOT_DIR / "client"
ART_DIR = ROOT_DIR / "ART"
HTTP_PORT = 8080


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def translate_path(self, path):
        """Serve /art/* out of ART/ so source art stays in one place instead
        of being duplicated into client/. Everything else falls through to
        the normal client/ root.
        """
        parsed = urllib.parse.urlsplit(path).path
        if parsed == "/art" or parsed.startswith("/art/"):
            rel = parsed[len("/art"):]
            # Reuse the base implementation's traversal-safe normalisation by
            # resolving against ART_DIR and confirming we stayed inside it.
            target = pathlib.Path(
                super().translate_path("/" + rel.lstrip("/"))
            )
            try:
                inside = target.relative_to(CLIENT_DIR)
            except ValueError:
                return super().translate_path(path)
            return str(ART_DIR / inside)
        return super().translate_path(path)

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
