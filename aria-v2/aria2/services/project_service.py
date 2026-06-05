"""services/project_service.py - Project workspaces."""

from __future__ import annotations

import json

from aria2.core import db
from aria2.core.ids import new_id, now_ms


def list_projects(include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM projects"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY pinned DESC, name"
    return [dict(r) for r in db.all(sql)]


def set_pinned(project_id: str, pinned: bool) -> None:
    update(project_id, {"pinned": 1 if pinned else 0})


def set_trust(project_id: str, level: str) -> None:
    """Set the project trust level: ask | accept | auto | plan."""
    allowed = {"ask", "accept", "auto", "plan"}
    update(project_id, {"trust_level": level if level in allowed else "ask"})


def counts(project_id: str) -> dict:
    """Cheap per-project tallies for the dashboard (chats / docs / automations)."""
    chats = db.one("SELECT COUNT(*) n FROM chats WHERE project_id=? AND archived=0",
                   (project_id,))["n"]
    docs = db.one("SELECT COUNT(*) n FROM documents WHERE project_id=?",
                  (project_id,))["n"]
    autos = db.one("SELECT COUNT(*) n FROM triggers WHERE project_id=?",
                   (project_id,))["n"]
    return {"chats": chats, "documents": docs, "automations": autos}


def get(project_id: str) -> dict | None:
    r = db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    return dict(r) if r else None


def create(name: str, folder: str = "", goals: str = "") -> dict:
    pid = new_id("prj")
    ts = now_ms()
    db.insert("projects", {
        "id": pid, "name": name, "folder": folder, "goals": goals,
        "settings_json": "{}", "archived": 0, "created_at": ts, "updated_at": ts,
    })
    return get(pid)


def update(project_id: str, changes: dict) -> None:
    allowed = {k: v for k, v in changes.items()
               if k in {"name", "folder", "goals", "archived", "pinned",
                        "trust_level", "settings_json"}}
    allowed["updated_at"] = now_ms()
    db.update("projects", project_id, allowed)


def archive(project_id: str, archived: bool = True) -> dict:
    if project_id == "general":
        return {"error": "The default project cannot be archived."}
    update(project_id, {"archived": 1 if archived else 0})
    return {"archived": archived}


def delete(project_id: str) -> dict:
    """Delete a project and (via FK cascade) its chats + messages. The default
    'general' project is protected."""
    if project_id == "general":
        return {"error": "The default project cannot be deleted."}
    db.delete("projects", project_id)
    return {"deleted": True}


def settings(project_id: str) -> dict:
    p = get(project_id)
    return json.loads(p["settings_json"]) if p else {}
