"""models/openai_provider.py - OpenAI adapter (streaming + tools).

Translates the neutral block format to/from OpenAI chat-completions, including
tool calls. Prompt caching on OpenAI is automatic server-side, so there's no
explicit cache_control to set.
"""

from __future__ import annotations

import json
import time
from typing import Iterator

from aria2.core import logs
from aria2.models.base import (
    Capabilities, StreamEvent, estimate_tokens, is_retryable, retry_sleep)

try:
    import openai

    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False

_PRICING = {
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None):
        if not AVAILABLE:
            raise RuntimeError("openai package not installed (pip install openai)")
        if not api_key:
            raise RuntimeError("No API key configured (Settings → Providers).")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url  # OpenAI-compatible endpoints (e.g. xAI Grok)
        self._client = openai.OpenAI(**kwargs)

    def capabilities(self, model: str) -> Capabilities:
        cin, cout = _PRICING.get(model, (2.5, 10.0))
        return Capabilities(
            context_window=128_000,
            supports_tools=True,
            supports_vision=True,
            supports_caching=True,
            input_cost_per_mtok=cin,
            output_cost_per_mtok=cout,
        )

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    @staticmethod
    def _to_openai(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            role, content = m["role"], m["content"]
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if role == "tool":
                for b in content:
                    if b.get("type") == "tool_result":
                        out.append({
                            "role": "tool",
                            "tool_call_id": b["tool_use_id"],
                            "content": b.get("content", ""),
                        })
                continue
            parts, tool_calls, has_image = [], [], False
            for b in content:
                t = b.get("type")
                if t == "text":
                    parts.append({"type": "text", "text": b["text"]})
                elif t == "image":
                    src = b.get("source", {})
                    if src.get("type") == "base64":
                        url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                        has_image = True
                elif t == "tool_use":
                    tool_calls.append({
                        "id": b["id"],
                        "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                    })
            # Vision content must be a parts list; otherwise collapse to a string.
            if has_image:
                msg_content = parts
            else:
                msg_content = "".join(p["text"] for p in parts if p["type"] == "text") or None
            msg: dict = {"role": role, "content": msg_content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        return out

    @staticmethod
    def _tools_to_openai(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

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
        kwargs = dict(
            model=model,
            messages=self._to_openai(system, messages),
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = self._tools_to_openai(tools)
        # Retry transient failures (429 / 5xx) only while nothing has streamed.
        for _attempt in range(4):
            try:
                yield from self._stream_once(kwargs)
                return
            except _OAIRetry as r:
                if r.yielded or not is_retryable(r.cause) or _attempt == 3:
                    yield StreamEvent(type="error", error=str(r.cause))
                    return
                logs.get("openai").warning(
                    logs.j("retry", attempt=_attempt, error=str(r.cause)))
                time.sleep(retry_sleep(_attempt))

    def _stream_once(self, kwargs: dict) -> Iterator[StreamEvent]:
        yielded = False
        try:
            partial: dict[int, dict] = {}
            stop_reason = "end_turn"
            usage = {}
            for chunk in self._client.chat.completions.create(**kwargs):
                if chunk.usage:
                    usage = {
                        "input": chunk.usage.prompt_tokens,
                        "output": chunk.usage.completion_tokens,
                    }
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    yielded = True
                    yield StreamEvent(type="text", text=delta.content)
                for tc in delta.tool_calls or []:
                    slot = partial.setdefault(
                        tc.index, {"id": "", "name": "", "args": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
                if choice.finish_reason == "tool_calls":
                    stop_reason = "tool_use"
            for slot in partial.values():
                try:
                    parsed = json.loads(slot["args"] or "{}")
                except Exception:
                    parsed = {}
                yielded = True
                yield StreamEvent(
                    type="tool_use",
                    tool_call={"id": slot["id"], "name": slot["name"], "input": parsed},
                )
            if usage:
                yield StreamEvent(type="usage", usage=usage)
            yield StreamEvent(type="done", stop_reason=stop_reason)
        except Exception as e:
            raise _OAIRetry(e, yielded)


class _OAIRetry(Exception):
    def __init__(self, cause: Exception, yielded: bool):
        super().__init__(str(cause))
        self.cause = cause
        self.yielded = yielded
