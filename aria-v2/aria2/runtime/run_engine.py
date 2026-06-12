"""runtime/run_engine.py - The durable agentic loop.

Every unit of agent work — a chat turn, a scheduled task, a delegated sub-task —
is a *run*: a persisted row with steps, token/cost accounting, a budget, and a
status you can inspect and cancel. The same engine powers the GUI, automations,
and delegation, so behaviour never diverges across surfaces (v1's bug).

Flow per run:
  1. assemble context (memory recall + knowledge + compaction)
  2. stream a model turn; persist a model step; publish token deltas
  3. if the model asked for tools: permission-check each, execute, persist a
     tool step, feed results back; loop
  4. stop on end_turn, max iterations, budget exhaustion, or cancellation
  5. finalise the run row (status, cost, tokens)

Events published on the bus (subscribed by the GUI / CLI):
  run.token   {run_id, text}
  run.tool    {run_id, name, input, phase: 'call'|'result', output}
  run.step    {run_id, step}
  run.status  {run_id, status}
  run.done    {run_id, text}
  run.error   {run_id, error}
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from aria2.core import db
from aria2.core.events import bus
from aria2.core.ids import new_id, now_ms
from aria2.models import registry as model_registry
from aria2.models.base import StreamEvent
from aria2.runtime.tools import permissions
from aria2.runtime.tools.base import ToolSet
from aria2.runtime.tools.registry import build_toolset

# Active cancellation flags, keyed by run_id, so any surface can stop a run.
_cancel: dict[str, threading.Event] = {}

# Pending dry-run overlays, keyed by run_id, awaiting commit/discard.
_sandboxes: dict[str, object] = {}


def cancel(run_id: str) -> None:
    ev = _cancel.get(run_id)
    if ev:
        ev.set()


def get_dry_run_diff(run_id: str) -> dict | None:
    sb = _sandboxes.get(run_id)
    return sb.diff() if sb else None


def commit_dry_run(run_id: str, git_commit: bool = False, message: str | None = None) -> dict:
    sb = _sandboxes.pop(run_id, None)
    if sb is None:
        return {"error": "no pending dry run for this run"}
    return sb.commit(git_commit=git_commit, message=message)


def discard_dry_run(run_id: str) -> dict:
    sb = _sandboxes.pop(run_id, None)
    return sb.discard() if sb else {"error": "no pending dry run"}


def dry_run_is_git(run_id: str) -> bool:
    sb = _sandboxes.get(run_id)
    return bool(sb and sb.is_git_repo())


@dataclass
class RunRequest:
    agent: dict                      # agents row
    project: dict                    # projects row
    messages: list[dict]             # neutral-format history incl. latest user turn
    kind: str = "chat"
    chat_id: str | None = None
    parent_run_id: str | None = None
    trigger_id: str | None = None
    overrides: dict = field(default_factory=dict)
    budget_usd: float | None = None
    include_shell: bool = True
    run_id: str | None = None        # caller may pre-assign so it can subscribe
    forked_from_run_id: str | None = None
    forked_from_step: int | None = None
    depth: int = 0                   # delegation depth (0 = top-level supervisor)
    dry_run: bool = False            # speculative: route effects to an overlay
    include_computer: bool = False   # offer mouse/keyboard/screen tools
    policy_overrides: dict = field(default_factory=dict)  # per-run tool allow/ask/deny
    fallback_to_cloud: bool = False  # retry with global cloud provider if local errors
    plan_only: bool = False          # plan mode: no tools, agent only explains


@dataclass
class RunResult:
    run_id: str
    status: str
    text: str
    cost_usd: float
    token_total: int
    assistant_content: list[dict]    # blocks for persistence (text + tool_use)
    predicted_diff: dict | None = None  # set for dry runs (files + captured cmds)


class RunEngine:
    def __init__(self, settings: dict):
        self._settings = settings

    # ── Public API ───────────────────────────────────────────────────────────

    def execute(self, req: RunRequest) -> RunResult:
        run_id = self._create_run(req)
        cancel_ev = threading.Event()
        _cancel[run_id] = cancel_ev
        bus.publish("run.status", {"run_id": run_id, "status": "running"})
        try:
            result = self._loop(run_id, req, cancel_ev)
        except Exception as e:
            self._finalise(run_id, "failed", error=str(e))
            bus.publish("run.error", {"run_id": run_id, "error": str(e)})
            result = RunResult(run_id, "failed", f"Error: {e}", 0.0, 0, [])
        finally:
            _cancel.pop(run_id, None)
        if req.dry_run and run_id in _sandboxes:
            result.predicted_diff = _sandboxes[run_id].diff()
            self._record_step(run_id, 99999, "dryrun", output=result.predicted_diff)
        # Cloud fallback: if the local run failed and fallback is enabled, retry
        # transparently with the global cloud provider (no local override).
        if (result.status == "failed" and req.fallback_to_cloud
                and req.overrides.get("provider") == "local"):
            bus.publish("run.token", {
                "run_id": run_id,
                "text": "\n\n*Local model unavailable — retrying with cloud provider…*\n\n",
            })
            cloud_req = RunRequest(
                agent=req.agent, project=req.project, messages=req.messages,
                kind=req.kind, chat_id=req.chat_id, run_id=new_id("run"),
                overrides={},   # use global settings (cloud provider)
                dry_run=req.dry_run, fallback_to_cloud=False,
                # Preserve the security envelope + run parameters of the original
                # request. Critical for messaging: a Telegram "restricted" session
                # must keep its tool restrictions when it falls back to cloud.
                include_shell=req.include_shell,
                include_computer=req.include_computer,
                policy_overrides=req.policy_overrides,
                depth=req.depth, budget_usd=req.budget_usd,
                trigger_id=req.trigger_id, parent_run_id=req.parent_run_id,
                plan_only=req.plan_only,
            )
            result = RunEngine(self._settings).execute(cloud_req)
        self._maybe_self_improve(result)
        return result

    def _maybe_self_improve(self, result: "RunResult") -> None:
        """On failure, ask the self-improvement service to file a fix proposal.
        Runs on a background thread; uses a one-shot completion, not a new run,
        so it can't recurse."""
        if result.status != "failed":
            return
        if not self._settings.get("self_improvement_enabled", False):
            return
        try:
            from aria2.services import self_improvement_service

            threading.Thread(
                target=self_improvement_service.analyze_failure,
                args=(result.run_id,), kwargs={"settings": self._settings},
                daemon=True, name="self-improve",
            ).start()
        except Exception:
            pass

    # ── Internals ─────────────────────────────────────────────────────────────

    def _loop(self, run_id: str, req: RunRequest, cancel_ev: threading.Event) -> RunResult:
        from aria2.runtime import context_compiler
        from aria2.services import knowledge_service, memory_service

        agent = req.agent
        last_user = _last_user_text(req.messages)

        # Model-neutral router: pick the best model for this task, then resolve.
        router_overrides = context_compiler.route(
            last_user, self._settings, req.overrides or None
        )
        merged = {**(req.overrides or {}), **router_overrides}
        provider, model = model_registry.for_settings(self._settings, merged)
        caps = provider.capabilities(model)

        scope = agent.get("memory_scope", "project")
        scope_id = "" if scope == "user" else (
            agent["id"] if scope == "agent" else req.project["id"]
        )
        base_dir = req.project.get("folder") or ""

        # Dry run: route file writes into a copy-on-write overlay and capture
        # (don't execute) shell commands, so nothing touches the real folder.
        sandbox = None
        if req.dry_run:
            from aria2.runtime.sandbox_overlay import OverlaySandbox
            sandbox = OverlaySandbox(base_dir or ".")
            _sandboxes[run_id] = sandbox

        # Retrieval first, so the memory tools can attach provenance (the recalled
        # facts become the derived_from set for anything the agent chooses to store).
        recalled, knowledge = [], []
        if last_user and scope != "none":
            recalled = memory_service.recall(last_user, scope=scope, scope_id=scope_id, limit=6)
            knowledge = knowledge_service.search(last_user, project_id=req.project["id"], limit=4)
        context_ids = [m["id"] for m in recalled]

        toolset, defaults = build_toolset(
            base_dir=base_dir, memory_scope=scope, memory_scope_id=scope_id,
            project_id=req.project["id"],
            include_shell=req.include_shell and caps.supports_tools,
            source_run_id=run_id, context_ids=context_ids,
            depth=req.depth, project=req.project, settings=self._settings,
            sandbox=sandbox, include_computer=req.include_computer,
        )
        # Per-run policy overrides (e.g. a Telegram "full access" session) take
        # precedence over the agent's own tool scopes in permission checks.
        agent_scopes = {**json.loads(agent.get("tool_scopes_json") or "{}"),
                        **(req.policy_overrides or {})}
        can_delegate = "delegate" in toolset.names()

        # Compile the optimal context window for this model.
        budget_tokens = min(
            self._settings.get("context_token_budget", 120_000),
            int(caps.context_window * 0.6),
        )
        compiled = context_compiler.compile_context(
            caps=caps,
            system_base=agent.get("system_prompt", "You are a helpful assistant.")
            + (f"\n\nThe project working folder is: {base_dir}\nUse it as the base "
               "for file and shell operations." if base_dir else ""),
            project_goals=req.project.get("goals", ""),
            recalled=recalled, knowledge=knowledge, history=list(req.messages),
            budget_tokens=budget_tokens,
            summariser=lambda t: self._oneshot(provider, model, t),
        )
        system = compiled.system
        # Only advertise tools in the system prompt when we'll actually pass tool
        # schemas to the model this run. Otherwise a weak local model (tools off,
        # e.g. llama3.2:3b) or plan mode would be told to call tools it can't
        # invoke — and small models then emit fake tool-call text (a bare `{}`)
        # instead of a normal reply.
        tools_active = caps.supports_tools and not req.plan_only
        if can_delegate and tools_active:
            system += (
                "\n\nYou can coordinate specialist agents. For a complex job, call "
                "suggest_agent to see who is best suited (rankings are learned from "
                "past performance), then delegate self-contained sub-tasks: use "
                "delegate for one, or delegate_parallel for several INDEPENDENT "
                "sub-tasks at once, and combine their results. Do simple work yourself."
            )
        # Make the agent aware it can reach the user proactively.
        names = set(toolset.names())
        reach = []
        if "notify_user" in names:
            reach.append("notify_user (message the user on Telegram)")
        if "post_discord" in names:
            reach.append("post_discord (post to a Discord channel)")
        if reach and tools_active:
            channels = " and ".join(reach)
            system += (
                f"\n\nTool available: {channels}."
                "\n\nIMPORTANT — how this works:"
                "\n• notify_user() SENDS the message directly to the user's phone via"
                " Telegram. ARIA handles the delivery. The user does NOT need to do"
                " anything manually — do NOT tell them to post or tag anything."
                "\n• ONLY call it when the user explicitly says something like:"
                " 'send on Telegram', 'message me', 'Telegram me', 'notify me'."
                "\n• Pass a plain string. Do NOT wrap in JSON or a dict."
                "\n"
                "\nCORRECT example — user says 'send a joke on Telegram':"
                "\n  → CALL: notify_user(\"Why did the scarecrow win an award?"
                " Because he was outstanding in his field!\")"
                "\n"
                "\nWRONG — do NOT do this:"
                "\n  × Tell the user to post it themselves"
                "\n  × Say 'tag @username on Telegram'"
                "\n  × Reply with JSON like {\"message\": \"...\"}"
            )
        messages = compiled.messages
        bus.publish("run.context", {"run_id": run_id, "included": compiled.included,
                                    "used_tokens": compiled.used_tokens, "model": model})

        max_iter = self._settings.get("max_iterations", 40)
        budget_usd = req.budget_usd if req.budget_usd is not None else self._settings.get(
            "default_run_budget_usd", 1.0
        )
        cost_total = 0.0
        token_total = 0
        assistant_content: list[dict] = []
        final_text = ""
        step_idx = 0

        for _iteration in range(max_iter):
            if cancel_ev.is_set():
                self._finalise(run_id, "cancelled", cost=cost_total, tokens=token_total)
                bus.publish("run.status", {"run_id": run_id, "status": "cancelled"})
                return RunResult(run_id, "cancelled", final_text, cost_total, token_total, assistant_content)

            # Snapshot the exact context this turn saw, so a fork can resume here.
            turn_input = [m for m in messages]
            text_buf = ""
            tool_calls: list[dict] = []
            usage = {}
            stop_reason = "end_turn"
            # Plan mode — no tools; agent explains without acting.
            tools_arg = (None if req.plan_only or not caps.supports_tools
                         else toolset.schemas)

            for ev in provider.stream(
                model=model,
                system=system,
                messages=messages,
                tools=tools_arg,
                max_tokens=self._settings.get("max_tokens", 4096),
                temperature=self._settings.get("temperature", 1.0),
                cache=self._settings.get("prompt_caching", True),
            ):
                if cancel_ev.is_set():
                    break
                if ev.type == "text":
                    text_buf += ev.text
                    bus.publish("run.token", {"run_id": run_id, "text": ev.text})
                elif ev.type == "tool_use":
                    tool_calls.append(ev.tool_call)
                elif ev.type == "clear_text":
                    # Local model wrote a tool call as text — discard it from
                    # the visible stream so the JSON doesn't show in the chat.
                    text_buf = ""
                    bus.publish("run.clear_text", {"run_id": run_id})
                elif ev.type == "usage":
                    usage = ev.usage
                elif ev.type == "done":
                    stop_reason = ev.stop_reason
                elif ev.type == "error":
                    self._finalise(run_id, "failed", error=ev.error, cost=cost_total, tokens=token_total)
                    bus.publish("run.error", {"run_id": run_id, "error": ev.error})
                    return RunResult(run_id, "failed", f"Error: {ev.error}", cost_total, token_total, assistant_content)

            # Cost accounting for this model turn.
            tin, tout = usage.get("input", 0), usage.get("output", 0)
            token_total += tin + tout
            cost_total += (
                tin / 1_000_000 * caps.input_cost_per_mtok
                + tout / 1_000_000 * caps.output_cost_per_mtok
            )
            step_idx += 1
            self._record_step(
                run_id, step_idx, "model", token_in=tin, token_out=tout,
                output={"text": text_buf[:2000], "stop_reason": stop_reason},
                messages=turn_input, system=system,
            )

            # Build assistant content blocks (text + tool_use) for this turn.
            turn_content: list[dict] = []
            if text_buf:
                turn_content.append({"type": "text", "text": text_buf})
                final_text = text_buf
            for tc in tool_calls:
                turn_content.append(
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
                )
            assistant_content = turn_content

            # Cancelled mid-stream: stop honestly as 'cancelled' (not 'done'), but
            # keep whatever text already streamed so the partial answer isn't lost.
            # Without this, a Stop during the final turn fell through and finalised
            # as 'done'.
            if cancel_ev.is_set():
                self._finalise(run_id, "cancelled", cost=cost_total, tokens=token_total)
                bus.publish("run.status", {"run_id": run_id, "status": "cancelled"})
                return RunResult(run_id, "cancelled", final_text, cost_total,
                                 token_total, turn_content)

            if stop_reason != "tool_use" or not tool_calls:
                self._finalise(run_id, "done", cost=cost_total, tokens=token_total)
                bus.publish("run.done", {"run_id": run_id, "text": final_text})
                return RunResult(run_id, "done", final_text, cost_total, token_total, turn_content)

            # Execute tools, gathering results to feed back.
            messages.append({"role": "assistant", "content": turn_content})
            results_content = []
            for tc in tool_calls:
                step_idx += 1
                out = self._run_tool(run_id, step_idx, tc, toolset, agent_scopes, defaults)
                results_content.append(
                    {"type": "tool_result", "tool_use_id": tc["id"],
                     "content": _tool_result_content(out, caps)}
                )
            messages.append({"role": "tool", "content": results_content})

            if cost_total > budget_usd:
                self._finalise(run_id, "failed", error="budget exceeded", cost=cost_total, tokens=token_total)
                bus.publish("run.error", {"run_id": run_id, "error": f"Budget ${budget_usd:.2f} exceeded."})
                return RunResult(run_id, "failed", final_text, cost_total, token_total, assistant_content)

        self._finalise(run_id, "failed", error="max iterations reached", cost=cost_total, tokens=token_total)
        bus.publish("run.error", {"run_id": run_id, "error": "Max iterations reached."})
        return RunResult(run_id, "failed", final_text, cost_total, token_total, assistant_content)

    def _run_tool(self, run_id, idx, tc, toolset: ToolSet, agent_scopes, defaults) -> dict:
        name, tool_input = tc["name"], tc["input"]
        bus.publish("run.tool", {"run_id": run_id, "name": name, "input": tool_input, "phase": "call"})
        tool = toolset.get(name)
        if tool is None:
            out = {"error": f"Unknown tool: {name}"}
        else:
            allowed, reason = permissions.check(
                name, tool_input, agent_scopes, defaults.get(name, "ask")
            )
            self._audit("tool_check", name, {"allowed": allowed, "reason": reason}, run_id)
            if not allowed:
                out = {"error": f"Tool '{name}' not permitted: {reason}"}
            else:
                t0 = now_ms()
                try:
                    out = tool.fn(**tool_input)
                except Exception as e:
                    out = {"error": f"Tool failed: {e}"}
                # Persist a slim copy: a tool may return an image (e.g. a
                # screenshot) for the model to see, but the base64 must not bloat
                # the run-step row / the bus event. It still reaches the model via
                # _tool_result_content below.
                self._record_step(
                    run_id, idx, "tool", tool_name=name,
                    input_data=tool_input, output=_strip_image(out),
                    duration_ms=now_ms() - t0,
                )
        bus.publish("run.tool", {"run_id": run_id, "name": name, "phase": "result",
                                 "output": _strip_image(out)})
        return out

    def _oneshot(self, provider, model, prompt: str) -> str:
        """One-shot, tool-free model call (used for summarisation)."""
        buf = ""
        for ev in provider.stream(
            model=model, system="You write dense, faithful summaries.",
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            tools=None, max_tokens=1024, cache=False,
        ):
            if ev.type == "text":
                buf += ev.text
            elif ev.type == "error":
                return "(summary unavailable)"
        return buf.strip()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _create_run(self, req: RunRequest) -> str:
        run_id = req.run_id or new_id("run")
        db.insert("runs", {
            "id": run_id, "kind": req.kind, "status": "running",
            "agent_id": req.agent["id"], "project_id": req.project["id"],
            "chat_id": req.chat_id, "parent_run_id": req.parent_run_id,
            "trigger_id": req.trigger_id, "title": req.agent.get("name", ""),
            "budget_usd": req.budget_usd or 0, "cost_usd": 0, "token_total": 0,
            "forked_from_run_id": req.forked_from_run_id,
            "forked_from_step": req.forked_from_step,
            "started_at": now_ms(),
        })
        return run_id

    def _record_step(self, run_id, idx, type_, tool_name=None, input_data=None,
                     output=None, token_in=0, token_out=0, duration_ms=0,
                     messages=None, system=None):
        # default=str so a non-JSON-serialisable tool output (or message snapshot)
        # degrades to its string form instead of crashing the whole run.
        snapshot = None
        if messages is not None:
            snapshot = json.dumps({"system": system, "messages": messages}, default=str)
        step = {
            "id": new_id("step"), "run_id": run_id, "idx": idx, "type": type_,
            "tool_name": tool_name,
            "input_json": json.dumps(input_data, default=str) if input_data is not None else None,
            "output_json": json.dumps(output, default=str) if output is not None else None,
            "messages_json": snapshot,
            "token_in": token_in, "token_out": token_out,
            "duration_ms": duration_ms, "created_at": now_ms(),
        }
        db.insert("run_steps", step)
        bus.publish("run.step", {"run_id": run_id,
                                 "step": {k: v for k, v in step.items() if k != "messages_json"}})

    def _finalise(self, run_id, status, cost=0.0, tokens=0, error=None):
        db.update("runs", run_id, {
            "status": status, "cost_usd": cost, "token_total": tokens,
            "error": error, "ended_at": now_ms(),
        })

    def _audit(self, action, target, detail, run_id):
        db.insert("audit_log", {
            "id": new_id("aud"), "actor": "engine", "action": action,
            "target": target, "detail_json": json.dumps(detail),
            "run_id": run_id, "created_at": now_ms(),
        })


def _strip_image(out):
    """Replace an inline `_image` (base64) with a short placeholder so the run
    step / bus event don't carry megabytes of image data."""
    if isinstance(out, dict) and "_image" in out:
        img = out.get("_image") or {}
        data = img.get("data", "") if isinstance(img, dict) else ""
        slim = {k: v for k, v in out.items() if k != "_image"}
        slim["image"] = (f"<{(img or {}).get('media_type', 'image')}, "
                         f"{len(data)} b64 chars (sent to model, not stored)>")
        return slim
    return out


def _tool_result_content(out, caps):
    """Build the tool_result `content` fed back to the model. If the tool returned
    an `_image` and the model can accept images inside a tool result, send a
    [text, image] block list so the model can SEE it; otherwise send JSON text
    (stripped of the base64) with a note."""
    img = out.get("_image") if isinstance(out, dict) else None
    if img and getattr(caps, "supports_image_tool_results", False) and img.get("data"):
        summary = {k: v for k, v in out.items() if k != "_image"}
        return [
            {"type": "text", "text": json.dumps(summary, default=str)},
            {"type": "image", "source": {
                "type": "base64",
                "media_type": img.get("media_type", "image/png"),
                "data": img["data"]}},
        ]
    if isinstance(out, dict) and "_image" in out:
        slim = {k: v for k, v in out.items() if k != "_image"}
        slim["note"] = ("Screenshot saved to 'path'; this model can't view images "
                        "in a tool result, so only the path is provided.")
        return json.dumps(slim, default=str)
    return json.dumps(out, default=str)


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                return " ".join(
                    b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
                )
            return str(c)
    return ""
