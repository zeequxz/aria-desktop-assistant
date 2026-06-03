"""models/ollama_provider.py - Local model adapter via Ollama.

Streams text. Tool calling support varies by local model, so we keep this path
text-only and let the engine fall back to a no-tools run for local providers.
"""

from __future__ import annotations

import json
from typing import Iterator

from aria2.models.base import Capabilities, StreamEvent, estimate_tokens

try:
    import requests

    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False


class OllamaProvider:
    name = "local"

    def __init__(self, url: str):
        if not AVAILABLE:
            raise RuntimeError("requests not installed (pip install requests)")
        self._url = url.rstrip("/")

    def capabilities(self, model: str) -> Capabilities:
        return Capabilities(
            context_window=8192,
            supports_tools=False,
            supports_vision=False,
            supports_caching=False,
        )

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    @staticmethod
    def _flatten(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            c = m["content"]
            if isinstance(c, list):
                c = " ".join(
                    b.get("text", b.get("content", "")) if isinstance(b, dict) else str(b)
                    for b in c
                )
            out.append({"role": m["role"] if m["role"] != "tool" else "user", "content": c})
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
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}] + self._flatten(messages),
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            with requests.post(
                f"{self._url}/api/chat", json=payload, stream=True, timeout=300
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield StreamEvent(type="text", text=chunk)
                    if data.get("done"):
                        break
            yield StreamEvent(type="done", stop_reason="end_turn")
        except requests.exceptions.ConnectionError:
            yield StreamEvent(
                type="error", error=f"Cannot reach Ollama at {self._url}. Is it running?"
            )
        except Exception as e:
            yield StreamEvent(type="error", error=str(e))
