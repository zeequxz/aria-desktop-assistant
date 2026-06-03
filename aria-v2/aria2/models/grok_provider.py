"""models/grok_provider.py - xAI Grok adapter.

Grok's API is OpenAI-compatible (chat/completions at api.x.ai/v1), so this
subclasses the OpenAI adapter and just points at xAI's base URL with Grok
pricing/limits. Streaming + tool calls come for free from the parent.
"""

from __future__ import annotations

from aria2.models.base import Capabilities
from aria2.models.openai_provider import OpenAIProvider

BASE_URL = "https://api.x.ai/v1"

_PRICING = {
    "grok-2-latest": (2.0, 10.0),
    "grok-2": (2.0, 10.0),
    "grok-beta": (5.0, 15.0),
}


class GrokProvider(OpenAIProvider):
    name = "grok"

    def __init__(self, api_key: str):
        super().__init__(api_key, base_url=BASE_URL)

    def capabilities(self, model: str) -> Capabilities:
        cin, cout = _PRICING.get(model, (2.0, 10.0))
        return Capabilities(
            context_window=131_072,
            supports_tools=True,
            supports_vision=False,
            supports_caching=False,
            input_cost_per_mtok=cin,
            output_cost_per_mtok=cout,
        )
