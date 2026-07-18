"""Tiny static file server for the client/ folder.
Runs alongside the GEP WebSocket server so the whole game is one
`python -m gep.server` command.
"""
import http.server
import pathlib
import threading

CLIENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "client"
HTTP_PORT = 8080


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise


def start_in_thread():
    server = http.server.HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server
