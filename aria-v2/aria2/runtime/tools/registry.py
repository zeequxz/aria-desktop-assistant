"""runtime/tools/registry.py - Assemble the ToolSet for a run.

Given the project base dir and the agent's memory scope, build the concrete set
of tools the agent may use. Tool *availability* is uniform; tool *permission* is
enforced per-call by permissions.check using the agent's scopes + each tool's
default policy. (A future step adds MCP connector tools here.)
"""

from __future__ import annotations

from aria2.runtime.tools.base import Tool, ToolSet
from aria2.runtime.tools.browser_tools import make_browser_tools
from aria2.runtime.tools.computer_tools import make_computer_tools
from aria2.runtime.tools.delegation_tools import make_delegation_tools
from aria2.runtime.tools.file_tools import make_file_tools
from aria2.runtime.tools.mcp_tools import make_mcp_tools
from aria2.runtime.tools.memory_tools import make_knowledge_tools, make_memory_tools
from aria2.runtime.tools.notify_tools import make_discord_tools, make_notify_tools
from aria2.runtime.tools.shell_tools import make_shell_tools


def build_toolset(
    base_dir: str,
    memory_scope: str,
    memory_scope_id: str,
    project_id: str,
    include_shell: bool = True,
    source_run_id: str | None = None,
    context_ids: list[str] | None = None,
    depth: int = 0,
    project: dict | None = None,
    settings: dict | None = None,
    sandbox=None,
    include_computer: bool = False,
) -> tuple[ToolSet, dict[str, str]]:
    """Return (toolset, default_policies) where default_policies maps tool name
    to its built-in default permission level.

    `source_run_id` / `context_ids` give the memory tools provenance. `depth` /
    `project` / `settings` gate and configure delegation: the supervisor tools
    are offered only while below max_delegation_depth, so the tree stays bounded.
    """
    settings = settings or {}
    dry = sandbox is not None
    tools: list[Tool] = []
    tools += make_file_tools(base_dir or ".", sandbox=sandbox)
    if include_shell:
        tools += make_shell_tools(base_dir or ".", dry_run_sandbox=sandbox)
    if memory_scope != "none":
        tools += make_memory_tools(memory_scope, memory_scope_id, source_run_id, context_ids)
    tools += make_knowledge_tools(project_id)

    # Web tools (read-only fetch/search default allow; open_url asks).
    if settings.get("browser_enabled", True):
        tools += make_browser_tools()

    # Outbound notification (Telegram) when the bridge is configured.
    if settings.get("messaging_enabled") and settings.get("telegram_bot_token"):
        tools += make_notify_tools()

    # Discord output channels when a webhook (default or named) is configured.
    if settings.get("discord_webhook_url") or settings.get("discord_channels"):
        tools += make_discord_tools()

    # Computer-use tools (mouse/keyboard/screen) — high risk, default "ask".
    # Offered when globally enabled or a run explicitly requests them (e.g. a
    # full-access Telegram session). Never during a dry run.
    if not dry and (include_computer or settings.get("computer_use_enabled", False)):
        tools += make_computer_tools()

    # Delegation and MCP have real side effects we can't roll back, so they are
    # withheld during a dry run (the overlay only governs files + captured shell).
    if (not dry
            and settings.get("delegation_enabled", True)
            and depth < settings.get("max_delegation_depth", 2)
            and project is not None):
        tools += make_delegation_tools(source_run_id or "", depth, project, settings)

    if not dry and settings.get("mcp_enabled", True):
        try:
            tools += make_mcp_tools()
        except Exception:
            pass  # never let a broken connector break a run

    defaults = {t.name: t.default_policy for t in tools}
    return ToolSet(tools), defaults
