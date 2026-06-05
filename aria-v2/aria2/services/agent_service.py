"""services/agent_service.py - Agent CRUD (the v1 hardcoded list, productised)."""

from __future__ import annotations

import json

from aria2.core import db
from aria2.core.ids import new_id, now_ms


def list_agents() -> list[dict]:
    return [dict(r) for r in db.all("SELECT * FROM agents ORDER BY builtin DESC, name")]


def get(agent_id: str) -> dict | None:
    r = db.one("SELECT * FROM agents WHERE id = ?", (agent_id,))
    return dict(r) if r else None


def get_by_name(name: str) -> dict | None:
    r = db.one("SELECT * FROM agents WHERE lower(name) = lower(?)", (name,))
    return dict(r) if r else None


def create(name: str, system_prompt: str, *, icon: str = "✦", color: str = "#6c8fff",
           description: str = "", provider: str | None = None, model: str | None = None,
           tool_scopes: dict | None = None, memory_scope: str = "project") -> dict:
    aid = new_id("agt")
    ts = now_ms()
    db.insert("agents", {
        "id": aid, "name": name, "icon": icon, "color": color,
        "description": description, "system_prompt": system_prompt,
        "provider": provider, "model": model,
        "tool_scopes_json": json.dumps(tool_scopes or {}),
        "memory_scope": memory_scope, "builtin": 0, "parent_agent_id": None,
        "version": 1, "created_at": ts, "updated_at": ts,
    })
    return get(aid)


def update(agent_id: str, changes: dict) -> None:
    if "tool_scopes" in changes:
        changes["tool_scopes_json"] = json.dumps(changes.pop("tool_scopes"))
    allowed = {k: v for k, v in changes.items() if k in {
        "name", "icon", "color", "description", "system_prompt", "provider",
        "model", "tool_scopes_json", "memory_scope",
    }}
    allowed["updated_at"] = now_ms()
    db.update("agents", agent_id, allowed)


def delete(agent_id: str) -> dict:
    a = get(agent_id)
    if a and a["builtin"]:
        return {"error": "Cannot delete a built-in agent."}
    db.delete("agents", agent_id)
    return {"deleted": True}


def overrides_for(agent: dict) -> dict:
    """Per-agent provider/model overrides for the run engine, if set."""
    ov = {}
    if agent.get("provider"):
        ov["provider"] = agent["provider"]
        if agent["provider"] == "claude" and agent.get("model"):
            ov["claude_model"] = agent["model"]
        elif agent["provider"] == "openai" and agent.get("model"):
            ov["openai_model"] = agent["model"]
        elif agent["provider"] == "local" and agent.get("model"):
            ov["ollama_model"] = agent["model"]
        elif agent["provider"] == "grok" and agent.get("model"):
            ov["grok_model"] = agent["model"]
        elif agent["provider"] == "gemini" and agent.get("model"):
            ov["gemini_model"] = agent["model"]
        elif agent["provider"] == "openai_compat" and agent.get("model"):
            ov["oai_compat_model"] = agent["model"]
    return ov
