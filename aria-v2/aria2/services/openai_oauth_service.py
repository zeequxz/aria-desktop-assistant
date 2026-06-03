"""services/openai_oauth_service.py - "Sign in with OpenAI" (Codex PKCE flow).

Ports v1's openai_oauth.py. Uses the same public OAuth constants as the
open-source Codex CLI (client id, endpoints, redirect port) so a user can
authenticate with their ChatGPT subscription account and use it as the OpenAI
provider instead of pasting a platform API key.

CAVEATS (same as v1):
  * Community-reverse-engineered flow, not officially sanctioned. OpenAI can
    change or block it at any time.
  * The access token targets the ChatGPT *backend* URL, not api.openai.com.
  * API keys remain the fully-supported method.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

from aria2.core import config
from aria2.core import secrets as _sec

# ── Codex OAuth constants (from the open-source Codex CLI) ───────────────────
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"
SCOPE = "openid profile email offline_access"
# The resulting bearer token targets this base URL (ChatGPT backend).
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/codex"


# ── PKCE helpers ─────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _build_authorize_url(challenge: str, state: str) -> str:
    params = {"response_type": "code", "client_id": CLIENT_ID,
              "redirect_uri": REDIRECT_URI, "scope": SCOPE,
              "code_challenge": challenge, "code_challenge_method": "S256",
              "state": state}
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


# ── Token storage (config + DPAPI encryption) ─────────────────────────────────

def _persist(tokens: dict) -> None:
    """Store tokens in config (encrypted via DPAPI for the sensitive fields)."""
    if "expires_in" in tokens:
        tokens["expires_at"] = int(time.time()) + int(tokens["expires_in"])
    s = config.load()
    s["openai_auth_mode"] = "oauth"
    s["openai_oauth_token"] = tokens.get("access_token", "")
    s["openai_oauth_refresh"] = tokens.get("refresh_token", "")
    s["openai_oauth_expires"] = int(tokens.get("expires_at", 0))
    config.save(s)


def sign_out() -> None:
    s = config.load()
    s["openai_auth_mode"] = "apikey"
    s["openai_oauth_token"] = ""
    s["openai_oauth_refresh"] = ""
    s["openai_oauth_expires"] = 0
    config.save(s)


def is_signed_in() -> bool:
    s = config.load()
    return (s.get("openai_auth_mode") == "oauth"
            and bool(s.get("openai_oauth_token")))


def get_account_id(settings: dict | None = None) -> str:
    """Extract the ChatGPT account id from the id_token or access_token JWT."""
    import base64
    s = settings or config.load()
    for key in ("openai_oauth_token",):
        raw = _sec.decrypt(s.get(key, "")) or s.get(key, "")
        if not raw:
            continue
        try:
            part = raw.split(".")[1]
            part += "=" * (-len(part) % 4)
            claims = json.loads(base64.urlsafe_b64decode(part))
            auth = claims.get("https://api.openai.com/auth", {})
            acct = auth.get("chatgpt_account_id", "")
            if acct:
                return acct
        except Exception:
            pass
    return ""


def get_display_name() -> str:
    """Decode the JWT id claim for a display-friendly account hint."""
    s = config.load()
    token = _sec.decrypt(s.get("openai_oauth_token", "")) or s.get("openai_oauth_token", "")
    if not token:
        return ""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(part))
        return claims.get("email") or claims.get("name") or "signed in"
    except Exception:
        return "signed in"


# ── Token exchange / refresh ─────────────────────────────────────────────────

def _post_token(payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode("ascii")
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _refresh() -> str | None:
    s = config.load()
    rt = _sec.decrypt(s.get("openai_oauth_refresh", "")) or s.get("openai_oauth_refresh", "")
    if not rt:
        return None
    try:
        new = _post_token({"grant_type": "refresh_token", "client_id": CLIENT_ID,
                           "refresh_token": rt})
        _persist(new)
        return new.get("access_token")
    except Exception:
        return None


def get_access_token(settings: dict | None = None) -> str:
    """Return a valid access token, refreshing if expired. Empty string if not
    signed in or refresh failed."""
    s = settings or config.load()
    if s.get("openai_auth_mode") != "oauth":
        return ""
    token = _sec.decrypt(s.get("openai_oauth_token", "")) or s.get("openai_oauth_token", "")
    expires = int(s.get("openai_oauth_expires") or 0)
    if token and expires and time.time() > (expires - 60):
        return _refresh() or token
    return token


# ── Callback HTTP server ──────────────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404); self.end_headers(); return
        _Handler.result = dict(urllib.parse.parse_qsl(parsed.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _Handler.result
        self.wfile.write((
            "<html><body style='font-family:sans-serif;background:#0b0d12;"
            "color:#eef1f6;text-align:center;padding-top:80px'>"
            f"<h2>{'✓ Signed in to ARIA' if ok else '✗ Sign-in failed'}</h2>"
            "<p>You can close this tab and return to ARIA.</p>"
            "</body></html>"
        ).encode("utf-8"))

    def log_message(self, *_):
        pass


# ── Main flow ─────────────────────────────────────────────────────────────────

def start_login(on_success: Callable[[dict], None],
                on_error: Callable[[str], None]) -> None:
    """Open the browser to the OpenAI sign-in page and exchange the code.
    Calls on_success(tokens) or on_error(message) on a background thread."""
    def _worker():
        verifier, challenge = _make_pkce()
        state = _b64url(secrets.token_bytes(16))
        _Handler.result = {}
        try:
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _Handler)
        except OSError as e:
            on_error(f"Couldn't open callback port {REDIRECT_PORT}: {e}")
            return
        server.timeout = 1
        webbrowser.open(_build_authorize_url(challenge, state))
        deadline = time.time() + 300
        try:
            while time.time() < deadline and not _Handler.result:
                server.handle_request()
        finally:
            server.server_close()
        result = _Handler.result
        if not result:
            on_error("Sign-in timed out. Please try again.")
            return
        if result.get("state") != state:
            on_error("State mismatch — possible CSRF. Please try again.")
            return
        if "code" not in result:
            on_error(result.get("error_description") or result.get("error") or "No code returned.")
            return
        try:
            tokens = _post_token({"grant_type": "authorization_code",
                                  "client_id": CLIENT_ID, "code": result["code"],
                                  "redirect_uri": REDIRECT_URI,
                                  "code_verifier": verifier})
        except Exception as e:
            on_error(f"Token exchange failed: {e}")
            return
        if "access_token" not in tokens:
            on_error("Token exchange returned no access token.")
            return
        _persist(tokens)
        on_success(tokens)

    threading.Thread(target=_worker, daemon=True, name="openai-oauth").start()
