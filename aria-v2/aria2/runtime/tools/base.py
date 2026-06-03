"""runtime/tools/base.py - Tool definition + registry.

A Tool bundles its callable, JSON schema, and a *default* permission level so
sensitive tools (shell, file write) default to "ask" while read-only tools
default to "allow". Agents can override per tool via their tool_scopes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., dict]
    default_policy: str = "ask"  # allow | ask | deny

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolSet:
    """A resolved set of tools available to one run."""

    def __init__(self, tools: list[Tool]):
        self._by_name = {t.name: t for t in tools}

    @property
    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._by_name.values()]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return list(self._by_name)
