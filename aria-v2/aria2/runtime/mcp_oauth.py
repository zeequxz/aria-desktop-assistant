"""runtime/mcp_oauth.py - OAuth 2.1 (auth-code + PKCE) for HTTP MCP servers.

Implements the browser-based authorization-code flow with PKCE that the MCP
authorization spec uses, plus token refresh. A short-lived localhost HTTP server
catches the redirect, so no secrets leave the machine and no cloud relay is
needed. Discovery of the authorization/token endpoints is attempted from the
server's `/.well-known/oauth-authorization-server`, with manual override.

Returns a token dict: {access_token, refresh_token, expires_at, token_url,
client_id, client_secret, scope} that connector_service persists (encrypted is
TODO; for now stored in the connectors row's auth_json).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url(os.urandom(40))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def discover(server_url: str) -> dict:
    """Fetch OAuth endpoints from the server's well-known metadata, if any."""
    import requests

    base = server_url.split("/mcp")[0].rstrip("/")
    for path in ("/.well-known/oauth-authorization-server",
                 "/.well-known/openid-configuration"):
        try:
            r = requests.get(base + path, timeout=10)
            if r.ok:
                meta = r.json()
                return {
                    "authorization_url": meta.get("authorization_endpoint", ""),
                    "token_url": meta.get("token_endpoint", ""),
                    "registration_url": meta.get("registration_endpoint", ""),
                }
        except Exception:
            continue
    return {}


def _catch_redirect(timeout: float) -> tuple[int, "queue.Queue"]:
    """Start a localhost server to receive the OAuth redirect. Returns (port, q)."""
    import queue
    from http.server import BaseHTTPRequestHandler, HTTPServer

    q: "queue.Queue" = queue.Queue()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q.put(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h3>Authorization complete. You can close this tab.</h3>")

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]

    def serve():
        httpd.handle_request()  # serve exactly one request (the redirect)
        httpd.server_close()

    threading.Thread(target=serve, daemon=True).start()
    return port, q


def authorize(authorization_url: str, token_url: str, client_id: str,
              client_secret: str = "", scope: str = "",
              timeout: float = 180.0) -> dict:
    """Run the interactive auth-code + PKCE flow. Blocks until the user
    authorizes in the browser (or times out). Returns a token dict."""
    import queue

    import requests

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    port, q = _catch_redirect(timeout)
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    params = {
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    webbrowser.open(authorization_url + "?" + urllib.parse.urlencode(params))

    try:
        result = q.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError("OAuth timed out waiting for authorization.")
    if result.get("state", [None])[0] != state:
        raise RuntimeError("OAuth state mismatch (possible CSRF).")
    code = result.get("code", [None])[0]
    if not code:
        raise RuntimeError(f"OAuth error: {result.get('error', ['unknown'])[0]}")

    data = {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri, "client_id": client_id,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    r = requests.post(token_url, data=data, timeout=30)
    r.raise_for_status()
    tok = r.json()
    return _store(tok, token_url, client_id, client_secret, scope)


def refresh(auth: dict) -> dict:
    """Refresh an expired access token using the stored refresh_token."""
    import requests

    if not auth.get("refresh_token") or not auth.get("token_url"):
        return auth
    data = {
        "grant_type": "refresh_token", "refresh_token": auth["refresh_token"],
        "client_id": auth.get("client_id", ""),
    }
    if auth.get("client_secret"):
        data["client_secret"] = auth["client_secret"]
    r = requests.post(auth["token_url"], data=data, timeout=30)
    r.raise_for_status()
    tok = r.json()
    merged = _store(tok, auth["token_url"], auth.get("client_id", ""),
                    auth.get("client_secret", ""), auth.get("scope", ""))
    # Some servers omit a new refresh_token on refresh — keep the old one.
    merged.setdefault("refresh_token", auth.get("refresh_token"))
    if not merged.get("refresh_token"):
        merged["refresh_token"] = auth.get("refresh_token")
    return merged


def is_expired(auth: dict, skew: int = 60) -> bool:
    exp = auth.get("expires_at")
    return bool(exp) and time.time() > (exp - skew)


def _store(tok: dict, token_url: str, client_id: str, client_secret: str,
           scope: str) -> dict:
    expires_at = None
    if tok.get("expires_in"):
        expires_at = time.time() + int(tok["expires_in"])
    return {
        "type": "oauth",
        "access_token": tok.get("access_token", ""),
        "refresh_token": tok.get("refresh_token"),
        "expires_at": expires_at,
        "token_url": token_url, "client_id": client_id,
        "client_secret": client_secret, "scope": scope,
    }
