"""services/provider_auth.py - Generic OAuth for model providers (OpenAI, Grok…).

One implementation, parameterised by a config key prefix (e.g. "openai",
"grok"). Reads/writes `{prefix}_oauth_*` settings and `{prefix}_auth_mode`, and
reuses the shared PKCE auth-code flow (runtime.mcp_oauth). Tokens are encrypted
at rest by config's secret handling.
"""

from __future__ import annotations

import time

from aria2.core import config


def _persist(prefix: str, tokens: dict) -> None:
    s = config.load()
    s[f"{prefix}_oauth_token"] = tokens.get("access_token", s.get(f"{prefix}_oauth_token", ""))
    if tokens.get("refresh_token"):
        s[f"{prefix}_oauth_refresh"] = tokens["refresh_token"]
    s[f"{prefix}_oauth_expires"] = int(tokens.get("expires_at") or 0)
    config.save(s)


def ensure_token(settings: dict | None, prefix: str) -> str:
    """Return a valid OAuth access token for `prefix`, refreshing if needed."""
    s = settings or config.load()
    if s.get(f"{prefix}_auth_mode") != "oauth":
        return ""
    token = s.get(f"{prefix}_oauth_token", "")
    expires = s.get(f"{prefix}_oauth_expires", 0) or 0
    refresh = s.get(f"{prefix}_oauth_refresh", "")
    token_url = s.get(f"{prefix}_oauth_token_url", "")
    if token and expires and time.time() > (expires - 60) and refresh and token_url:
        from aria2.runtime import mcp_oauth

        try:
            new = mcp_oauth.refresh({
                "refresh_token": refresh,
                "client_id": s.get(f"{prefix}_oauth_client_id", ""),
                "token_url": token_url,
                "scope": s.get(f"{prefix}_oauth_scope", ""),
            })
            _persist(prefix, new)
            token = new.get("access_token", token)
        except Exception:
            pass
    return token


def authorize(settings: dict | None, prefix: str) -> dict:
    """Run the interactive sign-in flow for `prefix` and store the tokens."""
    from aria2.runtime import mcp_oauth

    s = settings or config.load()
    auth_url = s.get(f"{prefix}_oauth_auth_url", "")
    token_url = s.get(f"{prefix}_oauth_token_url", "")
    client_id = s.get(f"{prefix}_oauth_client_id", "")
    if not auth_url or not token_url or not client_id:
        return {"error": "Set client id, authorization URL and token URL first."}
    try:
        tokens = mcp_oauth.authorize(auth_url, token_url, client_id,
                                     scope=s.get(f"{prefix}_oauth_scope", ""))
    except Exception as e:
        return {"error": str(e)}
    _persist(prefix, tokens)
    cur = config.load()
    cur[f"{prefix}_auth_mode"] = "oauth"
    config.save(cur)
    return {"ok": True, "expires_at": tokens.get("expires_at")}
