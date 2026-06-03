"""evals/harness.py - Run an EvalCase against the real engine and score it.

Because every run is durable (steps, tools, cost) and dry-runnable, scoring an
agent is just running a case and checking the recorded outcome. Checks operate
on the final text, the run's status, the tools it used, and — for file tasks —
the predicted diff from a dry run (so evals never mutate your machine).

Check specs (list of dicts), all must pass for the case to pass:
    {"type": "contains", "value": "391"}
    {"type": "not_contains", "value": "error"}
    {"type": "regex", "pattern": r"\\b391\\b"}
    {"type": "used_tool", "name": "write_file"}
    {"type": "created_file", "path": "hello.txt"}
    {"type": "no_error"}
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from aria2.core import config
from aria2.core.ids import new_id
from aria2.runtime import run_engine
from aria2.runtime.run_engine import RunEngine, RunRequest
from aria2.services import agent_service, project_service, run_service


@dataclass
class EvalCase:
    id: str
    prompt: str
    checks: list[dict]
    agent_id: str = "assistant"
    project_id: str = "general"
    dry_run: bool = False
    overrides: dict = field(default_factory=dict)


def _evaluate(checks: list[dict], text: str, status: str, steps: list[dict],
              diff: dict | None) -> list[dict]:
    text_l = (text or "").lower()
    used_tools = {s["tool_name"] for s in steps if s["type"] == "tool"}
    diff_files = {f["path"] for f in (diff or {}).get("files", [])}
    out = []
    for c in checks:
        t = c.get("type")
        if t == "contains":
            ok = c["value"].lower() in text_l
        elif t == "not_contains":
            ok = c["value"].lower() not in text_l
        elif t == "regex":
            ok = bool(re.search(c["pattern"], text or "", re.I))
        elif t == "used_tool":
            ok = c["name"] in used_tools
        elif t == "created_file":
            ok = c["path"] in diff_files
        elif t == "no_error":
            ok = status == "done"
        else:
            ok = False
        out.append({"check": c, "passed": ok})
    return out


def run_case(case: EvalCase, settings: dict | None = None) -> dict:
    settings = settings or config.load()
    agent = agent_service.get(case.agent_id) or agent_service.get("assistant")
    project = project_service.get(case.project_id) or project_service.get("general")
    rid = new_id("run")
    engine = RunEngine(settings)
    req = RunRequest(
        agent=agent, project=project,
        messages=[{"role": "user", "content": [{"type": "text", "text": case.prompt}]}],
        kind="eval", run_id=rid, dry_run=case.dry_run, overrides=case.overrides or {},
    )
    t0 = time.time()
    result = engine.execute(req)
    elapsed_ms = int((time.time() - t0) * 1000)
    steps = run_service.steps(rid)
    checks = _evaluate(case.checks, result.text, result.status, steps,
                       result.predicted_diff)
    if case.dry_run:
        run_engine.discard_dry_run(rid)  # evals never apply changes
    passed = all(c["passed"] for c in checks) if checks else (result.status == "done")
    return {
        "id": case.id, "passed": passed,
        "score": round(sum(c["passed"] for c in checks) / len(checks), 3) if checks else (1.0 if passed else 0.0),
        "status": result.status, "cost_usd": round(result.cost_usd, 4),
        "elapsed_ms": elapsed_ms, "run_id": rid, "checks": checks,
    }


def run_suite(cases: list[EvalCase], settings: dict | None = None) -> dict:
    results = [run_case(c, settings) for c in cases]
    n = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "total": n, "passed": passed,
        "pass_rate": round(passed / n, 3) if n else 0.0,
        "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "results": results,
    }


# ── Self-test (keyless): verifies scoring mechanics with a stub provider ────────

def self_test() -> dict:
    """Run a guaranteed-pass and guaranteed-fail case against a stub provider so
    the harness can be validated with no API key. Returns {pass_ok, fail_ok}."""
    from aria2.models import registry as model_registry
    from aria2.models.base import Capabilities, StreamEvent
    from aria2.runtime.tools import permissions

    class _Stub:
        name = "fake"

        def __init__(self):
            self._turn = 0

        def capabilities(self, model):
            return Capabilities(supports_tools=True, supports_caching=False)

        def count_tokens(self, text):
            return len(text) // 4

        def stream(self, model, system, messages, tools=None, max_tokens=4096,
                   temperature=1.0, cache=True):
            self._turn += 1
            if self._turn == 1:
                yield StreamEvent(type="tool_use", tool_call={
                    "id": "w1", "name": "write_file",
                    "input": {"path": "report.txt", "content": "done"}})
                yield StreamEvent(type="usage", usage={"input": 20, "output": 5})
                yield StreamEvent(type="done", stop_reason="tool_use")
            else:
                yield StreamEvent(type="text", text="Created report.txt successfully.")
                yield StreamEvent(type="usage", usage={"input": 10, "output": 5})
                yield StreamEvent(type="done", stop_reason="end_turn")

    permissions.set_approver(lambda *a: True)
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (_Stub(), "fake")
    try:
        st = {"prompt_caching": False, "max_iterations": 4,
              "delegation_enabled": False, "mcp_enabled": False}
        good = run_case(EvalCase(
            "selftest-pass", "make a report",
            [{"type": "created_file", "path": "report.txt"},
             {"type": "used_tool", "name": "write_file"},
             {"type": "contains", "value": "successfully"},
             {"type": "no_error"}], dry_run=True), st)
        bad = run_case(EvalCase(
            "selftest-fail", "make a report",
            [{"type": "contains", "value": "IMPOSSIBLE_TOKEN_XYZ"}], dry_run=True), st)
        return {"pass_ok": good["passed"], "fail_ok": not bad["passed"]}
    finally:
        model_registry.for_settings = orig
