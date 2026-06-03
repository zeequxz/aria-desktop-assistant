"""runtime/tools/memory_tools.py - Let the agent record and search memory.

These wrap the MemoryService so the agent can persist durable facts and recall
relevant ones on demand (in addition to the automatic recall the engine injects).
"""

from __future__ import annotations

from aria2.runtime.tools.base import Tool


def make_memory_tools(scope: str, scope_id: str, source_run_id: str | None = None,
                      context_ids: list[str] | None = None) -> list[Tool]:
    from aria2.services import memory_service

    def remember(text: str, importance: float = 0.6, kind: str = "semantic") -> dict:
        # Provenance: link the new fact to the current run and to the memories
        # that were in context when the agent decided to store it.
        m = memory_service.remember(
            text, scope=scope, scope_id=scope_id, importance=importance, kind=kind,
            source_run_id=source_run_id, derived_from=list(context_ids or []),
        )
        return {"stored": True, "id": m.get("id")}

    def recall(query: str, limit: int = 5) -> dict:
        hits = memory_service.recall(query, scope=scope, scope_id=scope_id, limit=limit)
        return {"results": [{"text": h["text"], "score": round(h["score"], 3)} for h in hits]}

    return [
        Tool(
            "remember",
            "Store a durable fact about the user or project in long-term memory. "
            "Use for preferences, names, recurring context, decisions.",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "importance": {"type": "number", "default": 0.6},
                    "kind": {
                        "type": "string",
                        "enum": ["semantic", "episodic", "preference"],
                        "default": "semantic",
                    },
                },
                "required": ["text"],
            },
            remember,
            default_policy="allow",
        ),
        Tool(
            "recall",
            "Search long-term memory for facts relevant to a query.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            recall,
            default_policy="allow",
        ),
    ]


def make_knowledge_tools(project_id: str) -> list[Tool]:
    from aria2.services import knowledge_service

    def search_knowledge(query: str, limit: int = 5) -> dict:
        hits = knowledge_service.search(query, project_id=project_id, limit=limit)
        return {
            "results": [
                {
                    "text": h["text"],
                    "source": h["title"],
                    "score": round(h["score"], 3),
                }
                for h in hits
            ]
        }

    return [
        Tool(
            "search_knowledge",
            "Search the project's indexed knowledge base (documents, code) and "
            "return relevant passages with their source for citation.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            search_knowledge,
            default_policy="allow",
        ),
    ]
