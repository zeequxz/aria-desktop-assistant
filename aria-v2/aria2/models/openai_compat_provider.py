"""models/openai_compat_provider.py - Generic OpenAI-compatible endpoint adapter.

For any server that speaks the OpenAI Chat Completions API:
  LM Studio · vLLM · llama.cpp server · LocalAI · KoboldCpp ·
  Text-Generation-WebUI · OpenRouter · and other OpenAI-compatible endpoints.

Unlike the Ollama adapter this carries a real API key (OpenRouter requires one)
and uses a fully user-supplied base URL. Streaming + tool calls are inherited
from OpenAIProvider; the context window and tool support are *configurable*
because they vary per server and model — and `oai_compat_tool_mode=never` gives
the graceful fallback for servers/models that don't support function calling.
"""

from __future__ import annotations

from aria2.models.base import Capabilities
from aria2.models.openai_provider import OpenAIProvider


def _normalize_base_url(url: str) -> str:
    """Accept a bare host ("http://localhost:1234") or a full OpenAI base URL and
    return one ending in the `/v1` path the OpenAI SDK expects. URLs that already
    end in `/v1` (e.g. OpenRouter's `/api/v1`) are left unchanged."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return base
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base


class OpenAICompatProvider(OpenAIProvider):
    """Any OpenAI-compatible chat endpoint with a configurable base URL + key."""

    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str = ""):
        base = _normalize_base_url(base_url)
        if not base:
            raise RuntimeError(
                "No OpenAI-compatible base URL configured (Settings → Providers).")
        # OpenAIProvider rejects an empty key; local servers don't need one, so
        # pass a harmless placeholder when the user left the key blank.
        super().__init__(api_key=api_key or "not-needed", base_url=base)

    def capabilities(self, model: str) -> Capabilities:
        from aria2.core import config

        # Tool calling: "never" turns it off for models that can't function-call;
        # "auto"/"always" enable it (the user pointed us at a server they chose).
        mode = config.get("oai_compat_tool_mode", "auto")
        supports_tools = mode != "never"
        try:
            ctx = int(config.get("oai_compat_num_ctx", 8192))
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
