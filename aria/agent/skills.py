"""
agent/skills.py - Skill learning (Hermes-style reusable workflows).

After ARIA completes a complex, multi-step task, the steps can be distilled
into a reusable "skill": a self-contained instruction you can re-run later by
name (from the chat, or exposed to the agent as a run_skill tool).

Storage is in settings under "skills": a list of
    {id, name, description, prompt, created}

This module is GUI-free: it provides CRUD helpers plus a summariser that turns
a finished conversation into a skill via a quick agent call.
"""

import uuid
from datetime import datetime

from config import settings as cfg


def list_skills() -> list:
    return cfg.get("skills", [])


def get_skill(skill_id: str):
    return next((s for s in cfg.get("skills", []) if s.get("id") == skill_id), None)


def find_skill(name: str):
    """Case-insensitive lookup by name."""
    name = (name or "").strip().lower()
    return next(
        (s for s in cfg.get("skills", []) if s.get("name", "").lower() == name), None
    )


def add_skill(name: str, prompt: str, description: str = "") -> dict:
    skill = {
        "id": f"skill_{uuid.uuid4().hex[:8]}",
        "name": name.strip(),
        "description": description.strip(),
        "prompt": prompt.strip(),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    skills = cfg.get("skills", [])
    skills.append(skill)
    cfg.set_key("skills", skills)
    return skill


def delete_skill(skill_id: str) -> bool:
    skills = cfg.get("skills", [])
    new = [s for s in skills if s.get("id") != skill_id]
    if len(new) != len(skills):
        cfg.set_key("skills", new)
        return True
    return False


def _conversation_text(messages: list, limit: int = 6000) -> str:
    """Flatten recent conversation messages to plain text for summarising."""
    parts = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            chunk = []
            for b in content:
                if isinstance(b, dict):
                    chunk.append(b.get("text", "") or b.get("name", "") or "")
                else:
                    chunk.append(str(b))
            content = " ".join(chunk)
        parts.append(f"{role.upper()}: {content}")
    text = "\n".join(parts)
    return text[-limit:]


def summarize_to_skill(messages: list) -> dict:
    """Distil a finished conversation into a reusable skill via a quick,
    tool-free agent call. Returns {name, description, prompt} or {error}."""
    if not messages:
        return {"error": "Nothing to summarise."}

    convo = _conversation_text(messages)
    instruction = (
        "Below is a conversation where an assistant completed a multi-step task. "
        "Distil it into a REUSABLE skill so the same kind of task can be repeated "
        "later on new inputs. Respond with EXACTLY three lines, nothing else:\n"
        "NAME: <a short title, 2-5 words>\n"
        "DESCRIPTION: <one sentence on what it does>\n"
        "PROMPT: <a single self-contained instruction that re-performs the "
        "workflow; use a {input} placeholder where the user's specific subject "
        "would go>\n\n"
        "Conversation:\n" + convo
    )

    # Lazy import to avoid a circular import at module load.
    from agent.orchestrator import run_agent_sync

    reply = run_agent_sync(
        instruction,
        system_prompt="You write concise, reusable task instructions.",
        use_computer_tools=False,
        use_browser_tools=False,
    )
    return _parse_skill_reply(reply)


def _parse_skill_reply(reply: str) -> dict:
    """Parse the NAME/DESCRIPTION/PROMPT reply into fields, tolerantly."""
    name, desc, prompt = "", "", ""
    if not reply:
        return {"error": "No summary produced."}
    lines = reply.splitlines()
    # PROMPT may span multiple lines; capture everything after the marker.
    mode = None
    for line in lines:
        upper = line.strip()
        low = upper.lower()
        if low.startswith("name:"):
            name = upper.split(":", 1)[1].strip()
            mode = "name"
        elif low.startswith("description:"):
            desc = upper.split(":", 1)[1].strip()
            mode = "desc"
        elif low.startswith("prompt:"):
            prompt = upper.split(":", 1)[1].strip()
            mode = "prompt"
        elif mode == "prompt" and line.strip():
            prompt += "\n" + line
    if not name:
        name = "New skill"
    if not prompt:
        # Fall back to using the whole reply as the prompt body.
        prompt = reply.strip()
    return {"name": name, "description": desc, "prompt": prompt.strip()}
