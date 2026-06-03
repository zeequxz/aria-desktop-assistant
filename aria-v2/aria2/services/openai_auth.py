"""services/openai_auth.py - "Sign in with OpenAI" (thin wrapper).

Delegates to the generic provider OAuth helper with the "openai" prefix. Kept as
a named module so existing imports (registry, settings) stay stable.
"""

from __future__ import annotations

from aria2.core import config
from aria2.services import provider_auth


def ensure_token(settings: dict | None = None) -> str:
    return provider_auth.ensure_token(settings or config.load(), "openai")


def authorize(settings: dict | None = None) -> dict:
    return provider_auth.authorize(settings or config.load(), "openai")
