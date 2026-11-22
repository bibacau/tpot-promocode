"""Tiny screenshot helper for running the app in Docker.

A container can't see your Windows desktop, so when the app runs in Docker this
helper runs on the host (double-click screen_agent.bat). It only takes
screenshots of a requested area and hands them to the app; the OCR and
everything else stays in the container.

Security: it listens on 127.0.0.1 only (Docker Desktop forwards
host.docker.internal there) and every request needs a random token, which is
written to data/screen_agent_token for the app to read through the mounted
data folder. Needs only `mss` and `pillow`.

    python -m salesbot.screen_agent [--port 8766] [--host 127.0.0.1] [--data data]
"""
from __future__ import annotations

import argparse
import hmac
import io
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TOKEN_FILE = "screen_agent_token"


def load_or_create_token(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    f = data_dir / TOKEN_FILE
    if f.exists() and f.read_text().strip():
        return f.read_text().strip()
    tok = secrets.token_urlsafe(32)
    f.write_text(tok)
    return tok


def default_grab(monitor: int, region: dict | None):
    from .screen_reader import grab
    return grab(monitor, region)


def default_monitors() -> list[dict]:
    from .screen_reader import list_monitors
    return list_monitors()


def make_handler(token: str, grab=default_grab, monitors=default_monitors):
    class Handler(BaseHTTPRequestHandler):
        server_version = "salesbot-screen-agent"

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code: int, msg: str) -> None:
            self._send(code, json.dumps({"detail": msg}).encode())

        def do_GET(self):  # noqa: N802
            if self.headers.get("Origin"):
                return self._err(403, "Browsers may not call the screen agent")
            if not hmac.compare_digest(self.headers.get("X-Agent-Token", ""), token):
                return self._err(401, "Bad or missing token")
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/health":
                    return self._send(200, b'{"ok": true}')
                if url.path == "/monitors":
                    return self._send(200, json.dumps({"monitors": monitors()}).encode())
                if url.path == "/grab":
                    region = None
                    if "width" in q:
                        region = {k: int(q[k]) for k in ("left", "top", "width", "height")}
                        if region["width"] <= 0 or region["height"] <= 0 or region["width"] * region["height"] > 40_000_000:
                            return self._err(400, "Bad region")
                    img = grab(int(q.get("monitor", 1)), region)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG", compress_level=1)
                    return self._send(200, buf.getvalue(), "image/png")
                return self._err(404, "Not found")
            except (ValueError, KeyError) as e:
                return self._err(400, str(e))
            except Exception as e:  # noqa: BLE001
                return self._err(500, f"{type(e).__name__}: {e}")

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Screenshot helper for the Dockerised AI Salesperson")
    ap.add_argument("--host", default="127.0.0.1",
                    help="Listen address. Keep 127.0.0.1 on Docker Desktop (Windows/Mac). "
                         "On Linux Docker use the docker0 bridge IP, e.g. 172.17.0.1.")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--data", default="data", help="Folder shared with the container (holds the token)")
    a = ap.parse_args(argv)
    token = load_or_create_token(Path(a.data))
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(token))
    print(f"Screen agent running on http://{a.host}:{a.port}. Leave this window open while streaming.")
    print(f"Token saved to {Path(a.data) / TOKEN_FILE} (the app reads it automatically).")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
