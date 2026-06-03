"""models/registry.py - Construct the right provider for a run.

`for_settings()` picks a provider from a settings dict (optionally overridden
per-agent or per-run), so the engine just asks the registry for a ready provider
and a model name and never touches provider-specific config.
"""

from __future__ import annotations

from aria2.models.anthropic_provider import AnthropicProvider
from aria2.models.base import Provider
from aria2.models.ollama_provider import OllamaProvider
from aria2.models.openai_provider import OpenAIProvider


def for_settings(s: dict, overrides: dict | None = None) -> tuple[Provider, str]:
    """Return (provider, model) for the given settings + optional overrides.

    Overrides may set provider/claude_model/openai_model/ollama_model to run a
    specific agent or task on a different model without changing globals.
    """
    s = {**s, **{k: v for k, v in (overrides or {}).items() if v}}
    provider = s.get("provider", "claude")

    if provider == "claude":
        return AnthropicProvider(s.get("claude_api_key", "")), s.get(
            "claude_model", "claude-opus-4-8"
        )
    if provider == "openai":
        if s.get("openai_auth_mode") == "oauth":
            from aria2.services import openai_oauth_service as _oai
            from aria2.models.codex_provider import CodexProvider
            token = _oai.get_access_token(s)
            account_id = _oai.get_account_id(s)
            return CodexProvider(token, account_id), s.get("openai_model", "gpt-5.5")
        key = s.get("openai_api_key", "")
        return OpenAIProvider(key), s.get("openai_model", "gpt-4o")
    if provider == "local":
        return OllamaProvider(s.get("ollama_url", "http://localhost:11434")), s.get(
            "ollama_model", "llama3"
        )
    if provider == "grok":
        from aria2.models.grok_provider import GrokProvider

        if s.get("grok_auth_mode") == "oauth":
            from aria2.services.provider_auth import ensure_token

            key = ensure_token(s, "grok") or s.get("grok_api_key", "")
        else:
            key = s.get("grok_api_key", "")
        return GrokProvider(key), s.get("grok_model", "grok-2-latest")
    if provider == "gemini":
        from aria2.models.gemini_provider import GeminiProvider

        return GeminiProvider(s.get("gemini_api_key", "")), s.get(
            "gemini_model", "gemini-2.0-flash"
        )
    raise RuntimeError(f"Unknown provider: {provider}")
