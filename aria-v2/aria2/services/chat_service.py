"""services/chat_service.py - Conversations, branching, and sending turns.

Owns chats + messages and is the entry point the GUI uses to talk to the agent.
`send_async` persists the user turn, runs the agent on a background thread (so
the UI stays responsive and tokens stream over the event bus), then persists the
assistant turn. `fork` clones a chat up to a chosen message — real branching that
v1 lacked.
"""

from __future__ import annotations

import base64
import json
import threading
from pathlib import Path

from aria2.core import config, db
from aria2.core.events import bus
from aria2.core.ids import new_id, now_ms
from aria2.runtime.run_engine import RunEngine, RunRequest, RunResult
from aria2.runtime import run_engine as _run_engine
from aria2.services import agent_service, memory_service, project_service


# ── Chats ────────────────────────────────────────────────────────────────────

def list_chats(project_id: str, include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM chats WHERE project_id = ?"
    if not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY pinned DESC, updated_at DESC"
    return [dict(r) for r in db.all(sql, (project_id,))]


def search_chats(project_id: str, query: str = "",
                 include_archived: bool = False) -> list[dict]:
    """Chats in a project, optionally filtered by a query that matches the title
    or any message content. Pinned first, then most-recent. Set include_archived
    to list the archive instead of active chats."""
    query = (query or "").strip().lower()
    arch = "1" if include_archived else "0"
    if not query:
        rows = db.all(
            f"SELECT * FROM chats WHERE project_id = ? AND archived = {arch} "
            "ORDER BY pinned DESC, updated_at DESC", (project_id,))
        return [dict(r) for r in rows]
    like = f"%{query}%"
    rows = db.all(
        "SELECT DISTINCT c.* FROM chats c "
        "LEFT JOIN messages m ON m.chat_id = c.id "
        f"WHERE c.project_id = ? AND c.archived = {arch} "
        "AND (lower(c.title) LIKE ? OR lower(m.content_json) LIKE ?) "
        "ORDER BY c.pinned DESC, c.updated_at DESC",
        (project_id, like, like),
    )
    return [dict(r) for r in rows]


def archive_chat(chat_id: str, archived: bool = True) -> None:
    db.update("chats", chat_id, {"archived": 1 if archived else 0, "updated_at": now_ms()})


def get_chat(chat_id: str) -> dict | None:
    r = db.one("SELECT * FROM chats WHERE id = ?", (chat_id,))
    return dict(r) if r else None


def create_chat(project_id: str, agent_id: str | None = None, title: str = "New chat") -> dict:
    cid = new_id("cht")
    ts = now_ms()
    db.insert("chats", {
        "id": cid, "project_id": project_id, "title": title,
        "agent_id": agent_id or config.get("active_agent", "assistant"),
        "parent_chat_id": None, "branch_point_message_id": None,
        "pinned": 0, "archived": 0, "created_at": ts, "updated_at": ts,
    })
    bus.publish("chat.created", {"chat_id": cid, "project_id": project_id})
    return get_chat(cid)


def rename_chat(chat_id: str, title: str) -> None:
    db.update("chats", chat_id, {"title": title, "updated_at": now_ms()})


def set_agent(chat_id: str, agent_id: str) -> None:
    db.update("chats", chat_id, {"agent_id": agent_id})


_READ_TOOLS  = {"read_file", "list_dir", "search_knowledge", "recall", "remember",
                "fetch_url", "web_search", "get_screen_size"}
_WRITE_TOOLS = {"write_file", "edit_file"}
_SHELL_TOOLS = {"run_shell", "run_python"}
_ALL_TOOLS   = _READ_TOOLS | _WRITE_TOOLS | _SHELL_TOOLS

_MODE_POLICIES: dict[str, dict[str, str]] = {
    # Ask — confirm everything (default, like a fresh Claude Code project).
    "ask":    {},
    # Accept — auto-allow reads + writes, ask for shell (safe for known projects).
    "accept": {**{t: "allow" for t in _READ_TOOLS | _WRITE_TOOLS},
               **{t: "ask"   for t in _SHELL_TOOLS}},
    # Auto — allow all tools in the project folder. Trust the model.
    "auto":   {t: "allow" for t in _ALL_TOOLS},
    # Plan — no tools at all; agent explains what it would do but doesn't act.
    "plan":   {t: "deny"  for t in _ALL_TOOLS},
}


def mode_policy(mode: str) -> dict[str, str]:
    """Return per-tool policy overrides for a chat/project mode."""
    return dict(_MODE_POLICIES.get(mode or "ask", {}))


def save_chat_settings(chat_id: str, provider_key: str, routing_key: str,
                       chat_mode: str = "") -> None:
    """Persist provider, routing (local/fallback), and permission mode per chat."""
    db.update("chats", chat_id, {
        "provider_key": provider_key or "",
        "exec_mode":    routing_key or "",
        "chat_mode":    chat_mode or "",
    })


def load_chat_settings(chat_id: str) -> tuple[str, str, str]:
    """Return (provider_key, routing_key, chat_mode) for this chat."""
    row = db.one(
        "SELECT provider_key, exec_mode, chat_mode FROM chats WHERE id=?",
        (chat_id,))
    if not row:
        return "", "", ""
    return (row["provider_key"] or "", row["exec_mode"] or "",
            row["chat_mode"] or "")


def set_pinned(chat_id: str, pinned: bool) -> None:
    db.update("chats", chat_id, {"pinned": 1 if pinned else 0})


def delete_chat(chat_id: str) -> None:
    db.delete("chats", chat_id)
    bus.publish("chat.deleted", {"chat_id": chat_id})


def fork(chat_id: str, up_to_message_id: str | None = None) -> dict:
    """Clone a chat into a new branch, copying messages up to (and including)
    `up_to_message_id` (or all messages if None)."""
    src = get_chat(chat_id)
    if not src:
        raise ValueError("chat not found")
    msgs = list_messages(chat_id)
    if up_to_message_id:
        cut = next((i for i, m in enumerate(msgs) if m["id"] == up_to_message_id), len(msgs) - 1)
        msgs = msgs[: cut + 1]

    new_cid = new_id("cht")
    ts = now_ms()
    db.insert("chats", {
        "id": new_cid, "project_id": src["project_id"],
        "title": f"{src['title']} (fork)", "agent_id": src["agent_id"],
        "parent_chat_id": chat_id, "branch_point_message_id": up_to_message_id,
        "pinned": 0, "archived": 0, "created_at": ts, "updated_at": ts,
    })
    for m in msgs:
        db.insert("messages", {
            "id": new_id("msg"), "chat_id": new_cid, "parent_id": None,
            "role": m["role"], "content_json": json.dumps(m["content"]),
            "model": m.get("model"), "token_in": 0, "token_out": 0, "cost_usd": 0,
            "created_at": now_ms(),
        })
    bus.publish("chat.created", {"chat_id": new_cid, "project_id": src["project_id"]})
    return get_chat(new_cid)


# ── Messages ──────────────────────────────────────────────────────────────────

def list_messages(chat_id: str, limit: int | None = None) -> list[dict]:
    """Messages oldest→newest. With `limit`, return only the most recent `limit`
    (still in chronological order) so the UI never loads an unbounded history."""
    # Tie-break on rowid (monotonic insertion order), not id — ids share a
    # millisecond prefix with a random suffix, so same-ms inserts would otherwise
    # come back out of insertion order.
    if limit:
        rows = db.all(
            "SELECT * FROM messages WHERE chat_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = list(reversed(rows))  # back to chronological order
    else:
        rows = db.all(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at, rowid",
            (chat_id,),
        )
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "role": r["role"],
            "content": json.loads(r["content_json"]),
            "model": r["model"], "cost_usd": r["cost_usd"],
            "created_at": r["created_at"],
        })
    return out


def delete_message(message_id: str) -> dict:
    """Delete a single message from a chat. Returns the chat_id it belonged to
    (so the caller can refresh) or an error."""
    row = db.one("SELECT chat_id FROM messages WHERE id = ?", (message_id,))
    if not row:
        return {"error": "Message not found."}
    chat_id = row["chat_id"]
    db.delete("messages", message_id)
    db.update("chats", chat_id, {"updated_at": now_ms()})
    bus.publish("message.deleted", {"chat_id": chat_id, "message_id": message_id})
    return {"deleted": True, "chat_id": chat_id}


def _persist_message(chat_id, role, content, model=None, token_in=0, token_out=0, cost=0.0) -> str:
    mid = new_id("msg")
    db.insert("messages", {
        "id": mid, "chat_id": chat_id, "parent_id": None, "role": role,
        "content_json": json.dumps(content), "model": model,
        "token_in": token_in, "token_out": token_out, "cost_usd": cost,
        "created_at": now_ms(),
    })
    db.update("chats", chat_id, {"updated_at": now_ms()})
    # Let the UI attach this id to the live bubble it just rendered, so
    # copy/fork/delete work on a just-sent message without a transcript reload.
    bus.publish("message.persisted",
                {"chat_id": chat_id, "message_id": mid, "role": role})
    return mid


def _history_for_engine(chat_id: str) -> list[dict]:
    """Messages in neutral format for the run engine (drop ids/metadata)."""
    return [{"role": m["role"], "content": m["content"]} for m in list_messages(chat_id)]


# ── Sending a turn ─────────────────────────────────────────────────────────────

_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp"}
_TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml",
              ".yml", ".html", ".css", ".java", ".go", ".rs", ".c", ".cpp", ".h",
              ".sh", ".sql", ".toml", ".ini", ".cfg", ".csv", ".log", ".xml"}
_MAX_TEXT_BYTES = 100_000
_MAX_IMAGE_BYTES = 5_000_000


def build_user_content(text: str, attachments: list[str] | None = None) -> list[dict]:
    """Turn a user message + file attachments into provider-neutral content
    blocks. Text/code files are inlined (capped); images become vision blocks;
    other/binary files get a labelled placeholder. Inlined content is data —
    never treated as instructions."""
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for path in attachments or []:
        p = Path(path)
        ext = p.suffix.lower()
        try:
            if ext in _IMAGE_TYPES and p.stat().st_size <= _MAX_IMAGE_BYTES:
                data = base64.b64encode(p.read_bytes()).decode("ascii")
                blocks.append({"type": "text", "text": f"[Attached image: {p.name}]"})
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": _IMAGE_TYPES[ext], "data": data}})
            elif ext in _TEXT_EXTS:
                raw = p.read_bytes()[:_MAX_TEXT_BYTES].decode("utf-8", "replace")
                note = " (truncated)" if p.stat().st_size > _MAX_TEXT_BYTES else ""
                blocks.append({"type": "text",
                               "text": f"[Attached file: {p.name}{note}]\n```\n{raw}\n```"})
            else:
                blocks.append({"type": "text",
                               "text": f"[Attached file: {p.name} — not inlined]"})
        except Exception as e:
            blocks.append({"type": "text", "text": f"[Could not read {p.name}: {e}]"})
    return blocks or [{"type": "text", "text": ""}]


def send_async(chat_id: str, user_text: str, on_complete=None, dry_run: bool = False,
               attachments: list[str] | None = None, overrides: dict | None = None,
               fallback_to_cloud: bool = False, chat_mode: str = "") -> str:
    """Persist the user turn and run the agent on a background thread.

    Returns the run_id immediately. Token/tool/done events arrive on the bus
    (topics run.token / run.tool / run.done / run.error, filtered by run_id).
    When `dry_run` is set, file/shell effects go to an overlay; call
    dry_run_diff/commit_dry_run/discard_dry_run with the run_id afterwards.
    """
    chat = get_chat(chat_id)
    if not chat:
        raise ValueError("chat not found")
    project = project_service.get(chat["project_id"])
    agent = agent_service.get(chat["agent_id"]) or agent_service.get("assistant")

    _persist_message(chat_id, "user", build_user_content(user_text, attachments))
    # Auto-title from the first user message (or the attachment if text-only).
    if chat["title"] in ("New chat", ""):
        title = user_text[:48] or (f"📎 {Path(attachments[0]).name}" if attachments else "")
        if title:
            rename_chat(chat_id, title)

    messages = _history_for_engine(chat_id)
    settings = config.load()
    engine = RunEngine(settings)
    run_id = new_id("run")
    # Tell the model manager which model is in use so idle timers are correct.
    try:
        from aria2.services import ollama_model_manager as _omm
        s = config.load()
        effective_provider = (overrides or {}).get("provider") or s.get("provider")
        if effective_provider == "local":
            model = ((overrides or {}).get("ollama_model")
                     or s.get("ollama_model", "llama3"))
            _omm.model_manager.ping(model)
    except Exception:
        pass
    agent_ov = agent_service.overrides_for(agent)
    merged = {**agent_ov, **(overrides or {})}

    # Determine effective mode: per-chat setting beats project trust level.
    effective_mode = chat_mode or project.get("trust_level", "ask") or "ask"
    policy_ov = mode_policy(effective_mode)
    plan_only = effective_mode == "plan"

    req = RunRequest(
        agent=agent, project=project, messages=messages, kind="chat",
        chat_id=chat_id, overrides=merged,
        run_id=run_id, dry_run=dry_run,
        fallback_to_cloud=fallback_to_cloud,
        policy_overrides=policy_ov,
        plan_only=plan_only,
    )

    def _worker():
        result: RunResult = engine.execute(req)
        if result.assistant_content:
            _persist_message(
                chat_id, "assistant", result.assistant_content,
                model=settings.get(f"{settings.get('provider')}_model"),
                cost=result.cost_usd,
            )
        _maybe_reflect(chat_id, agent, project, messages, result)
        if on_complete:
            on_complete(result)

    from aria2.runtime import executor
    executor.submit(_worker)
    return run_id


def cancel(run_id: str) -> None:
    """Stop an in-flight chat run."""
    _run_engine.cancel(run_id)


def dry_run_diff(run_id: str) -> dict | None:
    return _run_engine.get_dry_run_diff(run_id)


def commit_dry_run(run_id: str, git_commit: bool = False, message: str | None = None) -> dict:
    return _run_engine.commit_dry_run(run_id, git_commit=git_commit, message=message)


def discard_dry_run(run_id: str) -> dict:
    return _run_engine.discard_dry_run(run_id)


def dry_run_is_git(run_id: str) -> bool:
    return _run_engine.dry_run_is_git(run_id)


def _maybe_reflect(chat_id, agent, project, messages, result: RunResult) -> None:
    """Lightweight episodic-memory hook: on a successful, substantial turn, the
    engine could extract durable facts. Kept conservative — only stores an
    episodic breadcrumb so recall has something to find. Full LLM reflection is
    a later enhancement."""
    if result.status != "done" or not result.text:
        return
    scope = agent.get("memory_scope", "project")
    if scope == "none":
        return
    scope_id = "" if scope == "user" else (
        agent["id"] if scope == "agent" else project["id"]
    )
    # Store only when the user explicitly asked to remember something; extract the
    # bare fact (imperative stripped) so recall isn't polluted with "remember
    # that …". The agent also has the `remember` tool for deliberate storage.
    last_user = messages[-1]["content"] if messages else ""
    if isinstance(last_user, list):
        last_user = " ".join(b.get("text", "") for b in last_user if isinstance(b, dict))
    fact = memory_service.extract_memory_request(last_user)
    if fact:
        memory_service.remember(
            fact, scope=scope, scope_id=scope_id, importance=0.7, kind="episodic",
        )

    # Reflection (opt-in): on a substantial turn, extract durable user facts in the
    # background. Off by default — it costs an extra model call per turn.
    if config.get("memory_reflection", False):
        blob = f"User: {last_user}\nAssistant: {result.text}"
        if len(blob) > 200:
            import threading
            settings = config.load()
            threading.Thread(
                target=lambda: memory_service.reflect(
                    blob, scope, scope_id, settings, agent),
                daemon=True, name="reflect").start()
