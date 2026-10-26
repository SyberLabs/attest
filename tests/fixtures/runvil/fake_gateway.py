"""A stand-in for runvil-gateway, used by tests/test_cli_config.py. Offline, loopback only.

Usage, matching the flags the Gateway guide uses:

    fake_gateway.py --version
    fake_gateway.py --config gateway.toml --profile main [--unhealthy]

It reads ``bind = "127.0.0.1:PORT"`` from the config file (so it only starts when the
sidecar fixture was written), then serves:

    GET /health      -> 200 "ok"            (503 with --unhealthy, for the readiness timeout test)
    GET /v1/models   -> 401 {"error": {"code": "unauthorized"}} when a wrong bearer token is sent,
                        200 {"data": []} with the right token or no credential (loopback is trusted)
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

KEY = "secret-key"
VERSION = "runvil-gateway 0.0.0-fake"


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(VERSION, flush=True)
        return 0
    if "--config" not in argv:
        print("error: --config is required", file=sys.stderr, flush=True)
        return 2
    config = argv[argv.index("--config") + 1]
    try:
        text = open(config, encoding="utf-8").read()
    except OSError as exc:
        print(f"error: cannot read config {config}: {exc}", file=sys.stderr, flush=True)
        return 2
    m = re.search(r'bind\s*=\s*"127\.0\.0\.1:(\d+)"', text)
    if not m:
        print("error: config has no loopback bind", file=sys.stderr, flush=True)
        return 2
    port = int(m.group(1))
    profile = argv[argv.index("--profile") + 1] if "--profile" in argv else "default"
    unhealthy = "--unhealthy" in argv

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            return

        def _send(self, status: int, body: str, ctype: str = "application/json") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                if unhealthy:
                    self._send(503, json.dumps({"error": {"code": "model_loading"}}))
                else:
                    self._send(200, "ok", "text/plain")
                return
            if self.path == "/v1/models":
                auth = self.headers.get("Authorization")
                if auth is not None and auth != f"Bearer {KEY}":
                    self._send(401, json.dumps({"error": {"code": "unauthorized"}}))
                else:
                    self._send(200, json.dumps({"data": []}))
                return
            self._send(404, json.dumps({"error": {"code": "not_found"}}))

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"fake gateway: profile {profile}, serving on http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
