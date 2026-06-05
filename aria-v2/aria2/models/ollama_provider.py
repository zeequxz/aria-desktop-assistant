"""models/ollama_provider.py - Local model adapter via Ollama.

Uses Ollama's OpenAI-compatible endpoint (/v1/chat/completions) so that
streaming AND proper tool calling work for models that support it
(llama3.1+, qwen2.5, mistral, codellama etc.).

Previous approach used /api/chat with supports_tools=False — this caused the
model to write tool-calls as plain text (e.g. notify_user("...")) instead of
issuing real function calls. The OpenAI-compatible path fixes this.
"""

from __future__ import annotations

import json

from aria2.models.base import Capabilities
from aria2.models.openai_provider import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    """Local Ollama models via the OpenAI-compatible /v1 endpoint.

    Inherits streaming + tool-calling from OpenAIProvider, with two important
    overrides:
      1. Strips `stream_options` — Ollama /v1 doesn't support it and hangs
         if it's present, causing the 5-minute delay.
      2. Sets a longer connect timeout (model load can take 60–120 s on first
         call) while keeping a short read timeout per chunk.
    """

    name = "local"

    def __init__(self, url: str):
        base = url.rstrip("/")
        # Ollama doesn't validate the API key — any non-empty string works.
        super().__init__(api_key="ollama", base_url=f"{base}/v1")
        self._ollama_url = base

    def capabilities(self, model: str) -> Capabilities:
        from aria2.core import config
        from aria2.models.model_caps import ollama_tool_support

        # Tool-calling policy: auto-detect per model, or force on/off. "always"
        # lets non-Ollama OpenAI-compatible servers (vLLM, LM Studio, llama.cpp)
        # advertise tools for a capable model the detector doesn't recognise.
        mode = config.get("ollama_tool_mode", "auto")
        if mode == "always":
            supports_tools = True
        elif mode == "never":
            supports_tools = False
        else:
            supports_tools = ollama_tool_support(model)

        try:
            ctx = int(config.get("ollama_num_ctx", 8192))
        except (TypeError, ValueError):
            ctx = 8192

        return Capabilities(
            context_window=max(2048, ctx),
            supports_tools=supports_tools,
            supports_vision=False,
            supports_caching=False,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
        )

    def stream(self, model, system, messages, tools=None,
               max_tokens=4096, temperature=1.0, cache=True):
        """Override to strip stream_options (not supported by Ollama /v1)."""
        import json
        from aria2.models.base import StreamEvent

        kwargs = dict(
            model=model,
            messages=self._to_openai(system, messages),
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            # NO stream_options — Ollama hangs if this is present
            timeout=600,           # model load can be slow on first request
        )
        if tools:
            kwargs["tools"] = self._tools_to_openai(tools)
        try:
            partial: dict[int, dict] = {}
            stop_reason = "end_turn"
            text_buf = []
            for chunk in self._client.chat.completions.create(**kwargs):
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text_buf.append(delta.content)
                    yield StreamEvent(type="text", text=delta.content)
                for tc in delta.tool_calls or []:
                    slot = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
                if choice.finish_reason == "tool_calls":
                    stop_reason = "tool_use"

            # --- Ollama text-as-tool-call rescue ---
            # Some Ollama models (qwen2.5-coder, phi3 etc.) output tool calls as
            # plain JSON text instead of structured API tool_calls.  Detect this:
            # if the *entire* response is a JSON object with "name"+"arguments"
            # and no real structured call was made, treat it as a tool call.
            # We only do this when the full response is PURELY the JSON — partial
            # JSON mixed with prose is left as-is (don't over-intercept).
            if not partial:
                full = "".join(text_buf).strip()
                tc_obj = _parse_pure_tool_call(full)
                if tc_obj:
                    # The model wrote a tool call as text — execute it properly.
                    # Emit a special "clear_text" event so the UI can wipe the
                    # JSON fragment from the streaming bubble before showing the
                    # real tool result.
                    yield StreamEvent(type="clear_text", text="")
                    stop_reason = "tool_use"
                    partial["0"] = tc_obj

            for slot in partial.values():
                try:
                    parsed = json.loads(slot["args"] or "{}") if isinstance(slot["args"], str) else slot.get("input", {})
                except Exception:
                    parsed = {}
                yield StreamEvent(type="tool_use",
                                  tool_call={"id": slot.get("id", "tc_0"),
                                             "name": slot["name"],
                                             "input": parsed})
            yield StreamEvent(type="usage", usage={"input": 0, "output": 0})
            yield StreamEvent(type="done", stop_reason=stop_reason)
        except Exception as e:
            yield StreamEvent(type="error", error=str(e))


def _parse_pure_tool_call(text: str) -> dict | None:
    """Return a tool-slot dict if `text` is entirely a JSON tool-call object.

    Only matches when the WHOLE response is the JSON (nothing else) — avoids
    false-positives on normal responses that happen to contain JSON snippets.
    Handles both {"name":…,"arguments":{…}} and {"name":…,"input":{…}}.
    """
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("function")
    args = obj.get("arguments") or obj.get("input") or obj.get("args") or {}
    if not name:
        return None
    # Sanity check: name should look like a valid tool name, not a field.
    if not name.replace("_", "").isalnum() or len(name) > 60:
        return None
    args_str = json.dumps(args) if isinstance(args, dict) else (args or "{}")
    return {"id": "text_tc", "name": name, "args": args_str, "input": args}
