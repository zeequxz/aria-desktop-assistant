"""runtime/tools/permissions.py - Enforced tool policy (not a prompt string).

v1 relied on telling the model "confirm before destructive actions". Here the
dispatcher consults a real policy before executing any tool:

    deny  -> tool never runs
    ask   -> an approval callback decides (the GUI shows a dialog); no callback
             registered => treated as denied (safe default for headless runs)
    allow -> runs immediately

Resolution order: per-agent tool_scopes  ->  the tool's declared default
->  the global default_tool_policy. Every decision is auditable.
"""

from __future__ import annotations

from typing import Callable

from aria2.core import config

# decision = "allow" | "ask" | "deny"
ApprovalFn = Callable[[str, dict, str], bool]

# Process-wide approval handler installed by the UI; None => headless.
_approver: ApprovalFn | None = None


def set_approver(fn: ApprovalFn | None) -> None:
    global _approver
    _approver = fn


def resolve_policy(tool_name: str, agent_scopes: dict, tool_default: str) -> str:
    if tool_name in agent_scopes:
        return agent_scopes[tool_name]
    if tool_default:
        return tool_default
    return config.get("default_tool_policy", "ask")


def check(tool_name: str, tool_input: dict, agent_scopes: dict, tool_default: str) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    policy = resolve_policy(tool_name, agent_scopes, tool_default)
    if policy == "allow":
        return True, "allowed"
    if policy == "deny":
        return False, "denied by policy"
    # ask
    if _approver is None:
        return False, "approval required but no approver registered"
    approved = _approver(tool_name, tool_input, f"Run tool '{tool_name}'?")
    return (approved, "user approved" if approved else "user declined")
