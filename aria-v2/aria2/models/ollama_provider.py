"""models/ollama_provider.py - Local model adapter via Ollama.

Uses Ollama's OpenAI-compatible endpoint (/v1/chat/completions) so that
streaming AND proper tool calling work for models that support it
(llama3.1+, qwen2.5, mistral, codellama etc.).

Previous approach used /api/chat with supports_tools=False — this caused the
model to write tool-calls as plain text (e.g. notify_user("...")) instead of
issuing real function calls. The OpenAI-compatible path fixes this.
"""

from __future__ import annotations

import ast
import json
import re

from aria2.models.base import Capabilities, estimate_tokens
from aria2.models.openai_provider import OpenAIProvider


def _estimate_input_tokens(system: str, messages: list[dict]) -> int:
    """Approximate prompt tokens from the system prompt + message text. Ollama's
    OpenAI-compatible stream doesn't return usage, so we estimate rather than
    report 0."""
    parts = [system or ""]
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    parts.append(b.get("text") or b.get("content") or "")
    return estimate_tokens(" ".join(str(p) for p in parts))


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
            # Local models sometimes WRITE tool calls as text instead of emitting
            # structured tool_calls. When no real call was made, recover two shapes:
            #   (a) the whole reply is a single JSON tool-call object, or
            #   (b) the reply contains function-call syntax (often in ``` fences),
            #       e.g.  write_file(path="x", content="...")  — common with small
            #       qwen3 / llama models. Without this the calls just print as text
            #       and nothing executes (no file written, no Telegram sent).
            # Emit a "clear_text" event so the UI wipes the printed call from the
            # streaming bubble before the real tool result is shown.
            if not partial:
                full = "".join(text_buf).strip()
                tc_obj = _parse_pure_tool_call(full)
                if tc_obj:
                    yield StreamEvent(type="clear_text", text="")
                    stop_reason = "tool_use"
                    partial["0"] = tc_obj
                elif tools:
                    for i, c in enumerate(_extract_text_tool_calls(full, tools)):
                        if i == 0:
                            yield StreamEvent(type="clear_text", text="")
                            stop_reason = "tool_use"
                        partial[f"text_{i}"] = {
                            "id": f"text_{i}", "name": c["name"],
                            "args": json.dumps(c["input"]), "input": c["input"]}

            for slot in partial.values():
                try:
                    parsed = json.loads(slot["args"] or "{}") if isinstance(slot["args"], str) else slot.get("input", {})
                except Exception:
                    parsed = {}
                yield StreamEvent(type="tool_use",
                                  tool_call={"id": slot.get("id", "tc_0"),
                                             "name": slot["name"],
                                             "input": parsed})
            # Ollama's /v1 stream omits usage (stream_options hangs it), so token
            # counts would always read 0 — making the stats/inspector look broken.
            # Estimate from the text instead so local runs show believable numbers.
            yield StreamEvent(type="usage", usage={
                "input": _estimate_input_tokens(system, messages),
                "output": estimate_tokens("".join(text_buf))})
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


def _matching_paren(text: str, open_idx: int) -> int | None:
    """Index of the ')' matching the '(' at `open_idx`, respecting string literals
    (so parens inside quoted args don't throw off the balance)."""
    depth, i, in_str, esc = 0, open_idx, None, False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_str:
                in_str = None
        elif c in ("'", '"'):
            in_str = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _parse_call_args(call_src: str, schema: dict) -> dict:
    """Parse a `name(...)` snippet via the AST and return its arguments as a dict.
    Handles keyword args; a lone positional maps to the schema's first required
    field. Using the AST (not regex) means nested quotes / multiline string args
    parse correctly."""
    try:
        node = ast.parse(call_src.strip(), mode="eval").body
    except Exception:
        return {}
    if not isinstance(node, ast.Call):
        return {}
    out: dict = {}
    for kw in node.keywords:
        if kw.arg:
            try:
                out[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                pass
    if node.args and not out and len(node.args) == 1:
        props = list((schema.get("properties") or {}).keys())
        required = schema.get("required") or props
        target = required[0] if required else (props[0] if props else None)
        if target:
            try:
                out[target] = ast.literal_eval(node.args[0])
            except Exception:
                pass
    return out


def _extract_text_tool_calls(text: str, tools: list[dict]) -> list[dict]:
    """Recover tool calls a model wrote as TEXT — function-call syntax, often in
    ``` code fences — instead of emitting structured tool_calls. Returns
    [{name, input}] in source order. A match counts only when at least one
    argument parses, so a plain prose mention of a tool name is ignored."""
    schemas = {t["name"]: t.get("input_schema", {})
               for t in (tools or []) if t.get("name")}
    if not schemas:
        return []
    found: list[tuple[int, str, dict]] = []
    for name in schemas:
        for m in re.finditer(r"(?<![\w.])" + re.escape(name) + r"\s*\(", text):
            end = _matching_paren(text, m.end() - 1)
            if end is None:
                continue
            inp = _parse_call_args(name + text[m.end() - 1:end + 1], schemas[name])
            if inp:
                found.append((m.start(), name, inp))
    found.sort(key=lambda x: x[0])
    out: list[dict] = []
    seen: set = set()
    for _pos, name, inp in found:
        key = (name, json.dumps(inp, sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            out.append({"name": name, "input": inp})
    return out
