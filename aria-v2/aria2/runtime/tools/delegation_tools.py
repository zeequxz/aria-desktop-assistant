"""runtime/tools/delegation_tools.py - Supervisor → worker sub-agent delegation.

A supervisor agent can fan out work to specialist workers that run as durable,
parented **child runs** — in parallel — and combine their results. Unlike v1's
synchronous depth-2 recursion, here every sub-task is:

  * a real `runs` row (kind='delegated', parent_run_id set) → inspectable + a
    delegation tree in the Runs view,
  * executed concurrently via a thread pool,
  * scored into the routing stats so the org learns who's best at what.

Depth is bounded by max_delegation_depth so the tree can't explode. The tools
are only offered when the current run is below that depth.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from aria2.runtime.tools.base import Tool


def make_delegation_tools(parent_run_id: str, depth: int, project: dict,
                          settings: dict) -> list[Tool]:
    from aria2.services import agent_service, routing_service

    def _run_child(agent_name: str, task: str) -> dict:
        from aria2.runtime.run_engine import RunEngine, RunRequest

        agent = agent_service.get_by_name(agent_name) or agent_service.get(agent_name)
        if not agent:
            names = ", ".join(a["name"] for a in agent_service.list_agents())
            return {"agent": agent_name, "error": f"Unknown agent. Available: {names}"}
        engine = RunEngine(settings)
        req = RunRequest(
            agent=agent, project=project,
            messages=[{"role": "user", "content": [{"type": "text", "text": task}]}],
            kind="delegated", parent_run_id=parent_run_id,
            overrides=agent_service.overrides_for(agent),
            include_shell=True, depth=depth + 1,
        )
        result = engine.execute(req)
        # Learn from the outcome — this is the self-improving flywheel.
        routing_service.record(agent["id"], task, result.status, result.cost_usd)
        return {"agent": agent["name"], "status": result.status,
                "result": result.text, "cost_usd": round(result.cost_usd, 4),
                "run_id": result.run_id}

    def list_agents() -> dict:
        return {"agents": [
            {"name": a["name"], "description": a.get("description", "")}
            for a in agent_service.list_agents()
        ]}

    def suggest_agent(task: str) -> dict:
        recs = routing_service.recommendations(task)
        return {"task_type": recs[0]["task_type"] if recs else "general",
                "ranked": recs[:5]}

    def delegate(agent: str, task: str) -> dict:
        """Run one sub-agent and return its result."""
        return _run_child(agent, task)

    def delegate_parallel(tasks: list) -> dict:
        """Run several sub-agents concurrently and return all results.

        `tasks` is a list of {"agent": name, "task": instruction}. Independent
        sub-tasks run at the same time; the supervisor combines the results.
        """
        if not isinstance(tasks, list) or not tasks:
            return {"error": "Provide a non-empty list of {agent, task} items."}
        items = [(t.get("agent", "Assistant"), t.get("task", "")) for t in tasks][:6]
        results = [None] * len(items)
        with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
            futures = {pool.submit(_run_child, a, t): i for i, (a, t) in enumerate(items)}
            for fut in futures:
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    results[idx] = {"error": str(e)}
        return {"results": results}

    return [
        Tool(
            "list_agents",
            "List specialist agents you can delegate sub-tasks to.",
            {"type": "object", "properties": {}},
            list_agents, default_policy="allow",
        ),
        Tool(
            "suggest_agent",
            "Get a ranked recommendation of which agent is best for a task, based "
            "on learned performance history.",
            {"type": "object", "properties": {"task": {"type": "string"}},
             "required": ["task"]},
            suggest_agent, default_policy="allow",
        ),
        Tool(
            "delegate",
            "Delegate one self-contained sub-task to a specialist agent and get "
            "its result back. The sub-agent has no memory of this conversation, so "
            "make the task self-contained.",
            {"type": "object",
             "properties": {"agent": {"type": "string"}, "task": {"type": "string"}},
             "required": ["agent", "task"]},
            delegate, default_policy="allow",
        ),
        Tool(
            "delegate_parallel",
            "Delegate several INDEPENDENT sub-tasks to specialist agents at the "
            "same time (they run concurrently). Use for fan-out work, then combine "
            "the results yourself. Provide a list of {agent, task}.",
            {"type": "object",
             "properties": {"tasks": {
                 "type": "array",
                 "items": {"type": "object",
                           "properties": {"agent": {"type": "string"},
                                          "task": {"type": "string"}},
                           "required": ["agent", "task"]}}},
             "required": ["tasks"]},
            delegate_parallel, default_policy="allow",
        ),
    ]
