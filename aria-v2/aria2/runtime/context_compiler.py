"""runtime/context_compiler.py - Compile the best context for a given model.

The model-neutral moat: your memory + knowledge + history is a portable local
asset, and for each turn we *compile* the optimal context window for whatever
model is targeted — respecting that model's window and relative cost, and
prioritising what matters most.

Priority ladder (high → low), each trimmed to fit the budget:
    1. system / persona / project goals        (never dropped)
    2. recalled memory (most relevant first)
    3. knowledge-base passages (RAG, cited)
    4. recent conversation turns (verbatim)
    5. older turns (compacted to a summary if they don't fit)

It also exposes a heuristic router (`route`) that can pick a cheaper/stronger
model per task — something a single-model vendor structurally cannot offer.

Returns a CompiledContext describing exactly what went in, so the run inspector
can show *why* the model saw what it saw.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aria2.models.base import Capabilities, estimate_tokens
from aria2.runtime import context_engine


@dataclass
class CompiledContext:
    system: str
    messages: list[dict]
    used_tokens: int
    budget_tokens: int
    included: dict = field(default_factory=dict)  # counts per section, for the inspector
    compacted: bool = False


def _section_tokens(text: str) -> int:
    return estimate_tokens(text)


def compile_context(
    *,
    caps: Capabilities,
    system_base: str,
    project_goals: str,
    recalled: list[dict],
    knowledge: list[dict],
    history: list[dict],
    budget_tokens: int | None = None,
    summariser=None,
) -> CompiledContext:
    budget = budget_tokens or int(caps.context_window * 0.6)
    included = {"memory": 0, "knowledge": 0, "history": len(history)}

    # 1. System (persona + goals) — always included.
    parts = [system_base]
    if project_goals:
        parts.append(f"Project goals:\n{project_goals}")
    system_tokens = _section_tokens("\n\n".join(parts))
    remaining = budget - system_tokens

    # 2. Memory — greedily include highest-scored until ~30% of remaining.
    mem_lines, mem_budget = [], int(remaining * 0.30)
    spent = 0
    for m in recalled:
        line = f"  • {m['text']}"
        t = _section_tokens(line)
        if spent + t > mem_budget:
            break
        mem_lines.append(line)
        spent += t
        included["memory"] += 1
    if mem_lines:
        parts.append("[Relevant memory about the user/project:]\n" + "\n".join(mem_lines))
    remaining -= spent

    # 3. Knowledge — up to ~30% of what's left, cited by source.
    kn_lines, kn_budget = [], int(remaining * 0.30)
    spent = 0
    for k in knowledge:
        line = f"  • ({k.get('title','source')}) {k['text'][:500]}"
        t = _section_tokens(line)
        if spent + t > kn_budget:
            break
        kn_lines.append(line)
        spent += t
        included["knowledge"] += 1
    if kn_lines:
        parts.append("[Relevant knowledge-base passages (cite by source):]\n" + "\n".join(kn_lines))
    remaining -= spent

    system = "\n\n".join(parts)

    # 4/5. History — fit recent turns into the remainder; compact the rest.
    messages = list(history)
    compacted = False
    if context_engine.total_tokens(messages) > remaining and len(messages) > context_engine.KEEP_RECENT:
        if summariser is not None:
            messages = context_engine.compact(messages, summariser)
            compacted = True
        else:
            messages = messages[-context_engine.KEEP_RECENT:]
            compacted = True

    used = _section_tokens(system) + context_engine.total_tokens(messages)
    return CompiledContext(
        system=system, messages=messages, used_tokens=used,
        budget_tokens=budget, included=included, compacted=compacted,
    )


# ── Heuristic model router (the model-neutral edge) ─────────────────────────────

def route(task_text: str, settings: dict, agent_overrides: dict | None = None) -> dict:
    """Pick provider/model overrides for a task.

    Respects an explicit per-agent choice first. Otherwise applies cheap
    heuristics: trivial/short asks can use a cheaper model; code/long-reasoning
    asks prefer the strongest. Returns overrides for models.registry.for_settings.
    Conservative by default — only nudges within the active provider.
    """
    if agent_overrides:
        return agent_overrides
    if not settings.get("auto_route", False):
        return {}

    t = (task_text or "").lower()
    provider = settings.get("provider", "claude")
    n = len(t)
    heavy = any(k in t for k in ("refactor", "debug", "architecture", "prove", "analyze", "plan"))
    trivial = n < 80 and not heavy

    if provider == "claude":
        if trivial:
            return {"claude_model": "claude-haiku-4-5"}
        if heavy:
            return {"claude_model": settings.get("claude_model", "claude-opus-4-8")}
    elif provider == "openai":
        if trivial:
            return {"openai_model": "gpt-4o-mini"}
    return {}
