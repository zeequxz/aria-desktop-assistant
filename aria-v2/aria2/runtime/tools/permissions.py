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


_SHELL_TOOLS = {"run_shell", "run_python"}


def _danger(tool_name: str, tool_input: dict) -> str:
    """Reason string if a shell/python tool call is obviously destructive, else ''."""
    if tool_name not in _SHELL_TOOLS:
        return ""
    from aria2.runtime.tools import command_safety
    payload = tool_input.get("command") or tool_input.get("code") or ""
    bad, reason = command_safety.is_dangerous(str(payload))
    return reason if bad else ""


def check(tool_name: str, tool_input: dict, agent_scopes: dict, tool_default: str) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    policy = resolve_policy(tool_name, agent_scopes, tool_default)
    # Destructive shell/python is NEVER run silently: force an approval even when
    # policy is "allow" (auto/accept mode). With no approver — Telegram bridge,
    # automations — "ask" resolves to deny, which is the safe default.
    danger = _danger(tool_name, tool_input)
    if danger and policy == "allow":
        policy = "ask"
    if policy == "allow":
        return True, "allowed"
    if policy == "deny":
        return False, "denied by policy"
    # ask
    if _approver is None:
        return (False, f"blocked dangerous command ({danger}); approval required "
                       "but no approver" if danger else
                       "approval required but no approver registered")
    prompt = (f"⚠ This looks dangerous ({danger}). Run tool '{tool_name}'?"
              if danger else f"Run tool '{tool_name}'?")
    approved = _approver(tool_name, tool_input, prompt)
    return (approved, "user approved" if approved else "user declined")
