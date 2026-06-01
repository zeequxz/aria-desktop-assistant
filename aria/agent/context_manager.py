"""
agent/context_manager.py - Long-context auto-summarisation.

When a chat grows long, older turns are summarised and replaced with a compact
digest so the model never silently loses context or hits its window limit.

Heuristics (conservative defaults):
  MAX_TURNS  = 30  turns before offering to summarise
  KEEP_TURNS = 8   most recent turns to preserve verbatim after summarisation

The ContextManager is stateless; it operates on the chat's history_msgs list
and exposes:
  needs_summarisation(msgs)           -> bool
  summarise(msgs, system) -> (new_msgs, summary_text)
"""

from __future__ import annotations

MAX_TURNS = 30  # offer summarisation at this many turns
KEEP_TURNS = 8  # keep this many recent turns verbatim


def needs_summarisation(msgs: list) -> bool:
    """True when the conversation is long enough to benefit from summarisation."""
    return len(msgs) >= MAX_TURNS


def _conversation_text(msgs: list, limit: int = 8000) -> str:
    parts = []
    for m in msgs:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b)) for b in content
            )
        parts.append(f"{role.upper()}: {str(content)[:400]}")
    return "\n".join(parts)[-limit:]


def summarise(msgs: list, system_prompt: str = "") -> tuple[list, str]:
    """Summarise the older turns; return (new_msgs, summary_text).

    The returned new_msgs replaces the full history: a synthetic assistant
    message carries the summary, followed by the KEEP_TURNS most recent turns.
    """
    if len(msgs) <= KEEP_TURNS:
        return msgs, ""

    older = msgs[:-KEEP_TURNS]
    recent = msgs[-KEEP_TURNS:]
    convo_text = _conversation_text(older)

    instruction = (
        "Summarise the following conversation excerpt into a compact paragraph "
        "that preserves all important context, decisions, facts and outputs so "
        "the conversation can continue without losing information. Be specific "
        "and concrete — include file names, values, conclusions.\n\n" + convo_text
    )

    from agent.orchestrator import run_agent_sync

    summary = run_agent_sync(
        instruction,
        system_prompt="You write precise, information-dense conversation summaries.",
        use_computer_tools=False,
        use_browser_tools=False,
    )

    summary_msg = {
        "role": "assistant",
        "content": (f"[Conversation summary — earlier context compressed]\n{summary}"),
    }
    new_msgs = [summary_msg] + recent
    return new_msgs, summary
