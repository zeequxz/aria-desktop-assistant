"""
agent/openai_oauth.py - "Sign in with ChatGPT" (OpenAI Codex OAuth) for ARIA.

This implements the same OAuth 2.0 Authorization-Code-with-PKCE flow that the
open-source OpenAI Codex CLI uses, so a user can authenticate with their
ChatGPT (Plus/Pro/Team) account instead of pasting a platform API key.

IMPORTANT / HONEST CAVEATS
--------------------------
* This is a community-reverse-engineered flow, not an officially sanctioned
  third-party OAuth product. OpenAI could change or block it at any time, and
  using a subscription this way may be restricted by their terms in future
  (Anthropic already banned the equivalent for Claude). API keys remain the
  fully-supported method.
* The constants below (client id, endpoints, redirect port) are the public
  values embedded in the open-source Codex CLI.
* The resulting access token is a ChatGPT-subscription bearer token. It works
  against the ChatGPT *backend* base URL (CHATGPT_BASE_URL), NOT the standard
  api.openai.com platform endpoint. See agent/orchestrator.py for how it's used.

Only the login + token lifecycle lives here; it has no GUI dependency.
"""

import os
import json
import time
import base64
import hashlib
import secrets
import threading
import urllib.parse
import urllib.request
import http.server
import webbrowser
from pathlib import Path
from typing import Callable, Optional

# ── Public Codex OAuth constants (from the open-source Codex CLI) ─────────────
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"
SCOPE = "openid profile email offline_access"

# Base URL the resulting bearer token is valid against (ChatGPT backend, not
# the platform API). Kept here so it's easy to adjust if the path changes.
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/codex"

# Where tokens are cached. Mirrors Codex's own ~/.codex location so the two can
# coexist, but ARIA only ever reads/writes its own file.
TOKEN_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ARIA"
TOKEN_FILE = TOKEN_DIR / "openai_oauth.json"


# ── PKCE helpers ─────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    """URL-safe base64 without padding (per RFC 7636)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_pkce() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(challenge: str, state: str) -> str:
    """Construct the browser URL the user is sent to in order to approve."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


# ── Token storage ────────────────────────────────────────────────────────────

def save_tokens(tokens: dict):
    """Persist tokens, stamping an absolute expiry from expires_in."""
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    if "expires_in" in tokens:
        tokens["expires_at"] = int(time.time()) + int(tokens["expires_in"])
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)
    try:  # best-effort: keep the token file readable only by the user
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass


def load_tokens() -> Optional[dict]:
    if not TOKEN_FILE.exists():
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_tokens():
    """Sign out: remove the cached tokens."""
    try:
        TOKEN_FILE.unlink()
    except FileNotFoundError:
        pass


def is_signed_in() -> bool:
    return load_tokens() is not None


def _decode_jwt_claims(token: str) -> dict:
    """Decode a JWT payload WITHOUT verifying the signature (we only need the
    claims to read the account id; the token is already trusted, it's ours)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore padding
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def get_account_id() -> Optional[str]:
    """Extract the ChatGPT account id the Codex backend requires, from the
    'https://api.openai.com/auth' claim of the id_token (falling back to the
    access_token, which is also a JWT carrying the same claim)."""
    tokens = load_tokens()
    if not tokens:
        return None
    for key in ("id_token", "access_token"):
        claims = _decode_jwt_claims(tokens.get(key, "") or "")
        auth = claims.get("https://api.openai.com/auth", {})
        acct = auth.get("chatgpt_account_id")
        if acct:
            return acct
    return None


# ── Token exchange / refresh ─────────────────────────────────────────────────

def _post_token(payload: dict) -> dict:
    """POST to the token endpoint and return the parsed JSON."""
    data = urllib.parse.urlencode(payload).encode("ascii")
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _exchange_code(code: str, verifier: str) -> dict:
    return _post_token({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })


def refresh_tokens() -> Optional[dict]:
    """Use the stored refresh_token to get a fresh access token. Returns the
    updated token dict, or None if there's nothing to refresh / it failed."""
    tokens = load_tokens()
    if not tokens or "refresh_token" not in tokens:
        return None
    try:
        new = _post_token({
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": tokens["refresh_token"],
        })
    except Exception:
        return None
    # Refresh responses sometimes omit the refresh_token; keep the old one.
    merged = {**tokens, **new}
    save_tokens(merged)
    return merged


def get_access_token() -> Optional[str]:
    """Return a currently-valid access token, refreshing if it has expired.
    Returns None if the user isn't signed in."""
    tokens = load_tokens()
    if not tokens:
        return None
    expires_at = tokens.get("expires_at", 0)
    # Refresh a minute before actual expiry to avoid edge races.
    if expires_at and time.time() > expires_at - 60:
        tokens = refresh_tokens() or tokens
    return tokens.get("access_token")


# ── Local callback server + browser launch ───────────────────────────────────

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the single OAuth redirect and stashes the query params."""
    result = {}

    def do_GET(self):  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        _CallbackHandler.result = dict(urllib.parse.parse_qsl(parsed.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _CallbackHandler.result
        body = (
            "<html><body style='font-family:sans-serif;background:#0d0d14;"
            "color:#e4e4f0;text-align:center;padding-top:80px'>"
            f"<h2>{'✓ Signed in' if ok else '✗ Sign-in failed'}</h2>"
            "<p>You can close this tab and return to ARIA.</p>"
            "</body></html>"
        )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):  # silence default stderr logging
        pass


def start_login(on_success: Callable[[dict], None],
                on_error: Callable[[str], None]):
    """Run the full PKCE flow in a background thread:
    open the browser, wait for the localhost redirect, exchange the code, and
    persist tokens. Calls on_success(tokens) or on_error(message)."""

    def worker():
        verifier, challenge = _make_pkce()
        state = _b64url(secrets.token_bytes(16))
        _CallbackHandler.result = {}

        try:
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
        except OSError as e:
            on_error(f"Couldn't open the callback port {REDIRECT_PORT}: {e}")
            return
        server.timeout = 1  # so we can poll for a stop/timeout

        webbrowser.open(build_authorize_url(challenge, state))

        # Wait up to 5 minutes for the user to approve in the browser.
        deadline = time.time() + 300
        try:
            while time.time() < deadline and not _CallbackHandler.result:
                server.handle_request()
        finally:
            server.server_close()

        result = _CallbackHandler.result
        if not result:
            on_error("Sign-in timed out. Please try again.")
            return
        if result.get("state") != state:
            on_error("Sign-in failed: state mismatch (possible CSRF). Try again.")
            return
        if "code" not in result:
            on_error(f"Sign-in failed: {result.get('error_description', result.get('error', 'no code returned'))}")
            return

        try:
            tokens = _exchange_code(result["code"], verifier)
        except Exception as e:
            on_error(f"Token exchange failed: {e}")
            return
        if "access_token" not in tokens:
            on_error("Token exchange returned no access token.")
            return

        save_tokens(tokens)
        on_success(tokens)

    threading.Thread(target=worker, daemon=True).start()
