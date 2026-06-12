"""models/anthropic_provider.py - Claude adapter with streaming + prompt caching.

Two things v1 got wrong and this fixes:
  1. It used messages.create (no streaming) — here we stream token deltas.
  2. It re-billed the full prompt every turn — here we place cache_control
     breakpoints on the system prompt, the tool schemas, and the last stable
     history boundary, so repeated turns hit the cache (big cost/latency win on
     agentic loops).
"""

from __future__ import annotations

from typing import Iterator

import time

from aria2.models.base import (
    Capabilities, StreamEvent, estimate_tokens, is_retryable, retry_sleep)
from aria2.core import logs

try:
    import anthropic

    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False

# USD per 1M tokens (approximate list pricing; used only for the local cost meter).
_PRICING = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


class AnthropicProvider:
    name = "claude"

    def __init__(self, api_key: str):
        if not AVAILABLE:
            raise RuntimeError("anthropic package not installed (pip install anthropic)")
        if not api_key:
            raise RuntimeError("No Claude API key configured (Settings → Providers).")
        self._client = anthropic.Anthropic(api_key=api_key)

    def capabilities(self, model: str) -> Capabilities:
        cin, cout = _PRICING.get(model, (15.0, 75.0))
        return Capabilities(
            context_window=200_000,
            supports_tools=True,
            supports_vision=True,
            supports_caching=True,
            input_cost_per_mtok=cin,
            output_cost_per_mtok=cout,
        )

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    # ── Translation ──────────────────────────────────────────────────────────

    @staticmethod
    def _to_anthropic(messages: list[dict]) -> list[dict]:
        """Neutral block format is already Anthropic-shaped; pass through,
        collapsing tool messages into user-role tool_result blocks."""
        out: list[dict] = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "tool":
                out.append({"role": "user", "content": content})
            else:
                out.append({"role": role, "content": content})
        return out

    def stream(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        cache: bool = True,
    ) -> Iterator[StreamEvent]:
        sys_blocks = [{"type": "text", "text": system}]
        api_tools = list(tools or [])
        if cache:
            # Cache the (large, stable) system prompt and the tool schema block.
            sys_blocks[-1]["cache_control"] = {"type": "ephemeral"}
            if api_tools:
                api_tools[-1] = {**api_tools[-1], "cache_control": {"type": "ephemeral"}}

        msgs = self._to_anthropic(messages)
        if cache and msgs:
            # Cache up to the most recent stable turn so the next agentic
            # iteration re-reads history from cache rather than re-billing it.
            msgs = self._add_history_cache_breakpoint(msgs)

        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=sys_blocks,
            messages=msgs,
        )
        if api_tools:
            kwargs["tools"] = api_tools

        # Retry transient failures (429 / 5xx / overloaded) — but only while
        # nothing has been streamed yet, so a retry can never duplicate output.
        for _attempt in range(4):
            yielded = False
            try:
                yield from self._stream_once(kwargs)
                return
            except _Retry as r:
                if r.yielded or not is_retryable(r.cause) or _attempt == 3:
                    yield StreamEvent(type="error", error=str(r.cause))
                    return
                logs.get("anthropic").warning(
                    logs.j("retry", attempt=_attempt, error=str(r.cause)))
                time.sleep(retry_sleep(_attempt))

    def _stream_once(self, kwargs: dict):
        yielded = False
        try:
            with self._client.messages.stream(**kwargs) as stream:
                tool_inputs: dict[int, dict] = {}
                for event in stream:
                    et = event.type
                    if et == "content_block_start":
                        blk = event.content_block
                        if getattr(blk, "type", None) == "tool_use":
                            tool_inputs[event.index] = {
                                "id": blk.id,
                                "name": blk.name,
                                "input_json": "",
                            }
                    elif et == "content_block_delta":
                        d = event.delta
                        if getattr(d, "type", None) == "text_delta":
                            yielded = True
                            yield StreamEvent(type="text", text=d.text)
                        elif getattr(d, "type", None) == "input_json_delta":
                            if event.index in tool_inputs:
                                tool_inputs[event.index]["input_json"] += d.partial_json
                    elif et == "content_block_stop":
                        if event.index in tool_inputs:
                            import json

                            t = tool_inputs[event.index]
                            try:
                                parsed = json.loads(t["input_json"] or "{}")
                            except Exception:
                                parsed = {}
                            yielded = True
                            yield StreamEvent(
                                type="tool_use",
                                tool_call={"id": t["id"], "name": t["name"], "input": parsed},
                            )
                final = stream.get_final_message()
                usage = {
                    "input": final.usage.input_tokens,
                    "output": final.usage.output_tokens,
                    "cache_read": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
                    "cache_write": getattr(
                        final.usage, "cache_creation_input_tokens", 0
                    ) or 0,
                }
                yield StreamEvent(type="usage", usage=usage)
                yield StreamEvent(type="done", stop_reason=final.stop_reason or "end_turn")
        except Exception as e:
            # Hand control back to the retry loop, telling it whether anything
            # was already streamed (if so, a retry is unsafe → surface error).
            raise _Retry(e, yielded)

    @staticmethod
    def _add_history_cache_breakpoint(msgs: list[dict]) -> list[dict]:
        """Put an ephemeral cache_control on the last block of the second-to-last
        message so stable history is cached across agentic iterations."""
        if len(msgs) < 2:
            return msgs
        msgs = [dict(m) for m in msgs]
        target = msgs[-2]
        content = target.get("content")
        if isinstance(content, list) and content:
            content = [dict(b) if isinstance(b, dict) else b for b in content]
            last = content[-1]
            if isinstance(last, dict):
                last["cache_control"] = {"type": "ephemeral"}
                content[-1] = last
                target["content"] = content
                msgs[-2] = target
        return msgs


class _Retry(Exception):
    """Internal: carries a transient error + whether output already streamed
    (so the stream() retry loop only retries when nothing was emitted)."""

    def __init__(self, cause: Exception, yielded: bool):
        super().__init__(str(cause))
        self.cause = cause
        self.yielded = yielded
