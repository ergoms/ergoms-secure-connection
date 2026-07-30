#!/usr/bin/env python3
"""Minimal GitHub HTTPS reverse-proxy for YOUR VPS.

Safer than public mirrors: traffic GitHub <-> only your server.

Security:
  - hostname allowlist (GitHub family only)
  - optional shared secret (OPS_CONTENT_SECRET / --token)
  - no off-allowlist redirects
  - only GET/HEAD/POST (enough for git)
  - binds 127.0.0.1 by default (TLS terminator in front)

Run on VPS (behind Caddy/nginx with TLS on 443), then on the office PC:

  config.json -> worker_base_url = "https://git-proxy.example.com"
  creds/.env -> MODE=vps and OPS_CONTENT_SECRET=<same as on VPS>
  .\\ops-content.ps1 on

URL shape:
  https://YOUR_HOST/https/github.com/OWNER/REPO.git
"""

from __future__ import annotations

import argparse
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ALLOWED = {
    "github.com",
    "www.github.com",
    "api.github.com",
    "codeload.github.com",
    "gist.github.com",
    "ghcr.io",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "github.githubassets.com",
    "avatars.githubusercontent.com",
    "packages.github.com",
    "uploads.github.com",
}

ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    # do not forward our access token upstream
    "x-ops-content-token",
}

# Set in main()
REQUIRE_TOKEN = ""


class _AllowlistRedirect(HTTPRedirectHandler):
    """Follow redirects only while Location stays on ALLOWED hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        host = (urlparse(newurl).hostname or "").lower()
        if host not in ALLOWED:
            return None
        return HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )


_OPENER = build_opener(_AllowlistRedirect)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        # Avoid logging Authorization / long query strings with tokens
        try:
            path = self.path.split("?", 1)[0]
        except Exception:  # noqa: BLE001
            path = "?"
        print("%s - %s %s" % (self.address_string(), self.command, path))

    def _send_plain(self, code: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _check_token(self) -> bool:
        if not REQUIRE_TOKEN:
            return True
        got = self.headers.get("X-Ops-Content-Token", "") or ""
        auth = self.headers.get("Authorization", "") or ""
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip() or got
        if not got or not secrets.compare_digest(got, REQUIRE_TOKEN):
            self._send_plain(401, "unauthorized: set X-Ops-Content-Token\n")
            return False
        return True

    def do_GET(self) -> None:
        self._proxy()

    def do_HEAD(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._send_plain(405, "method not allowed\n")

    def do_PATCH(self) -> None:
        self._send_plain(405, "method not allowed\n")

    def do_DELETE(self) -> None:
        self._send_plain(405, "method not allowed\n")

    def do_OPTIONS(self) -> None:
        self._send_plain(405, "method not allowed\n")

    def _proxy(self) -> None:
        if self.command not in ALLOWED_METHODS:
            self._send_plain(405, "method not allowed\n")
            return
        if not self._check_token():
            return

        parts = self.path.split("?", 1)
        path = parts[0]
        query = parts[1] if len(parts) > 1 else ""
        segs = [s for s in path.split("/") if s]

        if len(segs) < 2 or segs[0] != "https":
            self._send_plain(
                200,
                "ops-content vps github_proxy\n"
                "usage: /https/<host>/<path>\n"
                "auth: X-Ops-Content-Token (if OPS_CONTENT_SECRET set)\n"
                "example: /https/github.com/git/git/info/refs?service=git-upload-pack\n",
            )
            return

        host = segs[1].lower()
        if host not in ALLOWED:
            self._send_plain(403, f"host not allowed: {host}\n")
            return

        target_path = "/" + "/".join(segs[2:])
        target = f"https://{host}{target_path}"
        if query:
            target += "?" + query

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            # Strip our gate Bearer; keep Basic / GitHub token headers for remotes
            if (
                key.lower() == "authorization"
                and REQUIRE_TOKEN
                and value.lower().startswith("bearer ")
                and secrets.compare_digest(value[7:].strip(), REQUIRE_TOKEN)
            ):
                continue
            headers[key] = value
        headers["Host"] = host

        body = None
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 0:
            if length > 64 * 1024 * 1024:
                self._send_plain(413, "body too large\n")
                return
            body = self.rfile.read(length)

        req = Request(target, data=body, headers=headers, method=self.command)
        try:
            with _OPENER.open(req, timeout=120) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() in HOP_BY_HOP or key.lower() == "content-encoding":
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
        except HTTPError as e:
            payload = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "text/plain"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except URLError as e:
            self._send_plain(502, f"upstream error: {e}\n")


def main() -> int:
    global REQUIRE_TOKEN
    ap = argparse.ArgumentParser(description="GitHub reverse proxy for VPS")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument(
        "--token",
        default=os.environ.get("OPS_CONTENT_SECRET", ""),
        help="Shared secret (or env OPS_CONTENT_SECRET). Empty = no auth (not recommended).",
    )
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print(
            f"WARNING: binding {args.host} exposes the relay; prefer 127.0.0.1 + Caddy TLS",
            flush=True,
        )

    REQUIRE_TOKEN = (args.token or "").strip()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"listening on http://{args.host}:{args.port}")
    if REQUIRE_TOKEN:
        print("auth: X-Ops-Content-Token required")
    else:
        print("auth: DISABLED — set OPS_CONTENT_SECRET or --token")
    print("put TLS terminator (Caddy/nginx) in front on :443")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
