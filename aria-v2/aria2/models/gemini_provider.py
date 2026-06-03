"""models/gemini_provider.py - Google Gemini adapter.

Gemini exposes an OpenAI-compatible endpoint, so this subclasses the OpenAI
adapter pointed at Google's base URL with Gemini pricing/limits. Auth is a
Google AI Studio API key (passed as the bearer key by the OpenAI client).
"""

from __future__ import annotations

from aria2.models.base import Capabilities
from aria2.models.openai_provider import OpenAIProvider

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_PRICING = {
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.0),
    "gemini-2.5-pro": (1.25, 10.0),
}


class GeminiProvider(OpenAIProvider):
    name = "gemini"

    def __init__(self, api_key: str):
        super().__init__(api_key, base_url=BASE_URL)

    def capabilities(self, model: str) -> Capabilities:
        cin, cout = _PRICING.get(model, (0.10, 0.40))
        return Capabilities(
            context_window=1_000_000,
            supports_tools=True,
            supports_vision=True,
            supports_caching=False,
            input_cost_per_mtok=cin,
            output_cost_per_mtok=cout,
        )
