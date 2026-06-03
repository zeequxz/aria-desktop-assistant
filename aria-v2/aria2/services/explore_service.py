"""services/explore_service.py - Counterfactual exploration.

Runs several strategies for the same goal as parallel *dry runs*, each in its
own copy-on-write overlay, then lets you compare the predicted outcomes and
commit the winner. This is tree-search over real actions rather than tokens —
made possible by the deterministic run engine + the dry-run sandbox + parallel
execution that aria-v2 already has. A session-bound or direct-acting agent
can't fork the world N ways and compare.

Each variant becomes a durable dry-run with its own run_id; the chosen one is
committed via the normal dry-run commit path, the rest discarded.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from aria2.core import config
from aria2.core.ids import new_id
from aria2.runtime import run_engine
from aria2.runtime.run_engine import RunEngine, RunRequest
from aria2.services import agent_service, project_service


def run_variants(project_id: str, base_prompt: str, variants: list[dict],
                 max_parallel: int = 4) -> list[dict]:
    """Run each variant as a parallel dry run. `variants` is a list of
    {"label", "prompt"(optional, overrides base), "agent"(optional)}.

    Returns a list of {label, run_id, status, text, cost_usd, diff} — the diffs
    are predicted (nothing applied). Commit one with commit_variant(run_id)."""
    project = project_service.get(project_id) or project_service.get("general")
    settings = config.load()

    def _one(v: dict) -> dict:
        agent = (agent_service.get_by_name(v["agent"]) if v.get("agent") else None) \
            or agent_service.get(config.get("active_agent", "assistant")) \
            or agent_service.get("assistant")
        prompt = v.get("prompt") or base_prompt
        rid = new_id("run")
        engine = RunEngine(settings)
        req = RunRequest(
            agent=agent, project=project,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            kind="chat", run_id=rid, dry_run=True,
            overrides=agent_service.overrides_for(agent),
        )
        result = engine.execute(req)
        return {
            "label": v.get("label", agent["name"]),
            "run_id": result.run_id, "status": result.status,
            "text": result.text, "cost_usd": round(result.cost_usd, 4),
            "diff": result.predicted_diff or {"files": [], "commands": [],
                                              "has_changes": False},
        }

    if not variants:
        return []
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(variants))) as pool:
        return list(pool.map(_one, variants))


def commit_variant(chosen_run_id: str, all_run_ids: list[str],
                   git_commit: bool = False) -> dict:
    """Commit the chosen variant's overlay; discard every other variant."""
    result = run_engine.commit_dry_run(chosen_run_id, git_commit=git_commit)
    for rid in all_run_ids:
        if rid != chosen_run_id:
            run_engine.discard_dry_run(rid)
    return result


def discard_all(run_ids: list[str]) -> None:
    for rid in run_ids:
        run_engine.discard_dry_run(rid)
