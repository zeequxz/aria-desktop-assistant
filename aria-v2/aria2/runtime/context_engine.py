"""runtime/context_engine.py - Token-aware context assembly + compaction.

Replaces v1's "summarise after 30 turns" guess with a budget the model actually
has to live within. Responsibilities:

  * estimate the token footprint of the message list,
  * when it exceeds the budget, compact the oldest turns into a single summary
    message while preserving the most recent turns verbatim,
  * build the recalled-memory + knowledge preamble that gets prepended to the
    system prompt.

Summarisation reuses the run engine's provider via a lightweight one-shot call.
"""

from __future__ import annotations

from aria2.models.base import estimate_tokens

KEEP_RECENT = 8


def _msg_tokens(m: dict) -> int:
    content = m.get("content", "")
    if isinstance(content, list):
        total = 0
        for b in content:
            if isinstance(b, dict):
                total += estimate_tokens(b.get("text", "") or b.get("content", "") or "")
            else:
                total += estimate_tokens(str(b))
        return total
    return estimate_tokens(str(content))


def total_tokens(messages: list[dict]) -> int:
    return sum(_msg_tokens(m) for m in messages)


def needs_compaction(messages: list[dict], budget: int) -> bool:
    return total_tokens(messages) > budget and len(messages) > KEEP_RECENT


def _plain_text(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(
                b.get("text", b.get("content", "")) if isinstance(b, dict) else str(b)
                for b in c
            )
        parts.append(f"{m.get('role', '').upper()}: {str(c)[:600]}")
    return "\n".join(parts)


def compact(messages: list[dict], summariser) -> list[dict]:
    """Compact old turns into one summary message + recent verbatim turns.

    `summariser(text) -> str` is supplied by the engine (a one-shot model call).
    """
    if len(messages) <= KEEP_RECENT:
        return messages
    older, recent = messages[:-KEEP_RECENT], messages[-KEEP_RECENT:]
    summary = summariser(
        "Summarise this conversation excerpt into a dense paragraph preserving "
        "all decisions, facts, file names and outputs so work can continue:\n\n"
        + _plain_text(older)
    )
    summary_msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": f"[Earlier conversation summary]\n{summary}"}
        ],
    }
    return [summary_msg] + recent


def build_memory_preamble(recalled: list[dict], knowledge: list[dict]) -> str:
    """Render recalled memories + knowledge passages for the system prompt."""
    out = []
    if recalled:
        out.append("[Relevant memory about the user/project:]")
        for m in recalled:
            out.append(f"  • {m['text']}")
    if knowledge:
        out.append("\n[Relevant knowledge-base passages (cite by source):]")
        for k in knowledge:
            out.append(f"  • ({k.get('title','source')}) {k['text'][:400]}")
    return "\n".join(out)
