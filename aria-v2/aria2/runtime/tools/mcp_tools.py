"""runtime/tools/mcp_tools.py - Expose MCP server tools in the tool registry.

Each enabled connector's tools become first-class aria-v2 Tools, so they flow
through the *same* permission gate, audit log, and run inspector as built-in
tools. Names are namespaced (mcp_<connector>_<tool>) to avoid collisions and to
keep within provider tool-name constraints. External tools default to "ask".
"""

from __future__ import annotations

import re

from aria2.runtime.tools.base import Tool


def _safe_name(slug: str, tool: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"mcp_{slug}_{tool}")
    return name[:64]


def make_mcp_tools() -> list[Tool]:
    from aria2.services import connector_service

    tools: list[Tool] = []
    for c in connector_service.list_enabled():
        try:
            specs = connector_service.tools_for(c["id"])
        except Exception:
            continue  # a broken connector must never break a run
        slug = connector_service.slug(c)
        for spec in specs:
            mcp_tool_name = spec.get("name")
            if not mcp_tool_name:
                continue
            schema = spec.get("inputSchema") or {"type": "object", "properties": {}}
            if "type" not in schema:
                schema = {"type": "object", "properties": {}}

            def fn(__cid=c["id"], __tool=mcp_tool_name, **kwargs):
                return connector_service.call(__cid, __tool, kwargs)

            tools.append(Tool(
                name=_safe_name(slug, mcp_tool_name),
                description=f"[{c['name']}] {spec.get('description', '')}".strip(),
                input_schema=schema,
                fn=fn,
                default_policy="ask",
            ))
    return tools
