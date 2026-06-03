"""services/connector_service.py - Manage MCP connectors and live sessions.

Connectors are persisted rows; live MCPClient sessions are cached in-process and
started on demand, then reused across runs (starting a subprocess per run would
be wasteful). Tool lists are cached on the client. Everything degrades safely:
a dead/missing server yields an error from test_connection and contributes no
tools to a run rather than crashing it.
"""

from __future__ import annotations

import json
import re
import threading

from aria2.core import db
from aria2.core import secrets as _secrets
from aria2.core.ids import new_id, now_ms
from aria2.runtime.mcp_client import HTTPMCPClient, MCPClient, MCPError

_live: dict[str, MCPClient] = {}
_lock = threading.RLock()

# Sensitive auth fields encrypted at rest (DPAPI on Windows) — never plaintext.
_AUTH_SECRETS = ("token", "access_token", "refresh_token", "client_secret")


def _encode_auth(auth: dict) -> str:
    out = dict(auth or {})
    for k in _AUTH_SECRETS:
        if isinstance(out.get(k), str) and out[k]:
            out[k] = _secrets.encrypt(out[k])
    return json.dumps(out)


def _decode_auth(auth_json: str | None) -> dict:
    out = json.loads(auth_json or "{}")
    for k in _AUTH_SECRETS:
        if isinstance(out.get(k), str) and out[k]:
            out[k] = _secrets.decrypt(out[k])
    return out


# ── CRUD ────────────────────────────────────────────────────────────────────

def list_connectors() -> list[dict]:
    return [dict(r) for r in db.all("SELECT * FROM connectors ORDER BY name")]


def list_enabled() -> list[dict]:
    return [dict(r) for r in db.all("SELECT * FROM connectors WHERE enabled=1 ORDER BY name")]


def get(connector_id: str) -> dict | None:
    r = db.one("SELECT * FROM connectors WHERE id=?", (connector_id,))
    return dict(r) if r else None


def create(name: str, command: str, args: list[str] | None = None,
           env: dict | None = None, transport: str = "stdio",
           url: str = "", enabled: bool = True, auth: dict | None = None) -> dict:
    cid = new_id("con")
    db.insert("connectors", {
        "id": cid, "name": name, "transport": transport, "command": command,
        "args_json": json.dumps(args or []), "env_json": json.dumps(env or {}),
        "url": url, "auth_json": _encode_auth(auth or {"type": "none"}),
        "enabled": 1 if enabled else 0, "created_at": now_ms(),
    })
    return get(cid)


def update(connector_id: str, changes: dict) -> None:
    if "args" in changes:
        changes["args_json"] = json.dumps(changes.pop("args"))
    if "env" in changes:
        changes["env_json"] = json.dumps(changes.pop("env"))
    if "auth" in changes:
        changes["auth_json"] = _encode_auth(changes.pop("auth"))
    allowed = {k: v for k, v in changes.items() if k in {
        "name", "transport", "command", "args_json", "env_json", "url",
        "auth_json", "enabled",
    }}
    db.update("connectors", connector_id, allowed)
    _drop_live(connector_id)  # config changed → restart session next use


def _get_auth(connector_id: str) -> dict:
    c = get(connector_id)
    return _decode_auth(c["auth_json"]) if c else {}


# Public, decrypted auth accessor for the UI (so it never sees ciphertext).
def read_auth(connector_id: str) -> dict:
    return _get_auth(connector_id)


def _set_auth(connector_id: str, auth: dict) -> None:
    db.update("connectors", connector_id, {"auth_json": _encode_auth(auth)})


def auth_headers(connector_id: str) -> dict:
    """Compute Authorization headers for an HTTP connector, refreshing an
    expired OAuth token (and persisting it) on the fly."""
    from aria2.runtime import mcp_oauth

    auth = _get_auth(connector_id)
    atype = auth.get("type", "none")
    if atype == "bearer" and auth.get("token"):
        return {"Authorization": f"Bearer {auth['token']}"}
    if atype == "oauth" and auth.get("access_token"):
        if mcp_oauth.is_expired(auth) and auth.get("refresh_token"):
            try:
                auth = mcp_oauth.refresh(auth)
                auth["type"] = "oauth"
                _set_auth(connector_id, auth)
            except Exception:
                pass
        return {"Authorization": f"Bearer {auth['access_token']}"}
    return {}


def begin_oauth(connector_id: str) -> dict:
    """Run the interactive OAuth flow for a connector and store the tokens.
    Blocking — callers (the UI) should run this on a background thread."""
    from aria2.runtime import mcp_oauth

    c = get(connector_id)
    if not c:
        return {"error": "not found"}
    auth = _decode_auth(c["auth_json"])
    endpoints = {"authorization_url": auth.get("authorization_url", ""),
                 "token_url": auth.get("token_url", "")}
    if not endpoints["authorization_url"] or not endpoints["token_url"]:
        endpoints = {**endpoints, **{k: v for k, v in mcp_oauth.discover(c["url"] or "").items() if v}}
    if not endpoints.get("authorization_url") or not endpoints.get("token_url"):
        return {"error": "Missing authorization_url/token_url and discovery failed."}
    try:
        tokens = mcp_oauth.authorize(
            endpoints["authorization_url"], endpoints["token_url"],
            client_id=auth.get("client_id", ""),
            client_secret=auth.get("client_secret", ""),
            scope=auth.get("scope", ""),
        )
    except Exception as e:
        return {"error": str(e)}
    # Preserve the flow config alongside the tokens.
    tokens.update({k: auth.get(k, "") for k in
                   ("authorization_url", "client_id", "client_secret", "scope")})
    tokens["token_url"] = endpoints["token_url"]
    _set_auth(connector_id, tokens)
    _drop_live(connector_id)
    return {"ok": True, "expires_at": tokens.get("expires_at")}


def delete(connector_id: str) -> None:
    _drop_live(connector_id)
    db.delete("connectors", connector_id)


# ── Live sessions ──────────────────────────────────────────────────────────────

def slug(connector: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "_", connector["name"].lower()).strip("_")[:20] or "mcp"


def _client(connector: dict):
    with _lock:
        c = _live.get(connector["id"])
        if c is None or not c.is_alive():
            if connector["transport"] == "http":
                # env holds optional static headers; auth_headers adds (and
                # refreshes) the Authorization header per request.
                cid = connector["id"]
                c = HTTPMCPClient(
                    url=connector.get("url") or "",
                    headers=json.loads(connector["env_json"] or "{}"),
                    name=slug(connector),
                    headers_provider=lambda _cid=cid: auth_headers(_cid),
                )
            else:
                c = MCPClient(
                    command=connector["command"],
                    args=json.loads(connector["args_json"] or "[]"),
                    env=json.loads(connector["env_json"] or "{}"),
                    name=slug(connector),
                )
            _live[connector["id"]] = c
        return c


def _drop_live(connector_id: str) -> None:
    with _lock:
        c = _live.pop(connector_id, None)
        if c:
            c.stop()


def tools_for(connector_id: str) -> list[dict]:
    """Discover (and cache) the tools a connector exposes."""
    c = get(connector_id)
    if not c or c["transport"] not in ("stdio", "http"):
        return []
    return _client(c).list_tools()


def call(connector_id: str, tool_name: str, arguments: dict) -> dict:
    c = get(connector_id)
    if not c:
        return {"error": "connector not found"}
    try:
        return _client(c).call_tool(tool_name, arguments)
    except MCPError as e:
        return {"error": str(e)}


def test_connection(connector_id: str) -> dict:
    """Start the server and list its tools — used by the Connectors UI."""
    c = get(connector_id)
    if not c:
        return {"error": "not found"}
    if c["transport"] not in ("stdio", "http"):
        return {"error": f"Unsupported transport: {c['transport']}"}
    try:
        client = _client(c)
        client.start()
        tools = client.list_tools(refresh=True)
        return {"ok": True, "tools": [{"name": t.get("name"),
                                       "description": t.get("description", "")}
                                      for t in tools]}
    except MCPError as e:
        return {"error": str(e)}


def shutdown_all() -> None:
    with _lock:
        for c in _live.values():
            c.stop()
        _live.clear()
