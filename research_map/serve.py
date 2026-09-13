"""Static server for web/ (port from $PORT, default 8766)."""
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

port = int(os.environ.get("PORT", "8766"))
handler = partial(SimpleHTTPRequestHandler, directory=str(Path(__file__).parent / "web"))
print(f"serving web/ on http://localhost:{port}", flush=True)
ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
