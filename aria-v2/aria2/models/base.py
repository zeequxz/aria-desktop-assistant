"""models/base.py - Provider interface and shared types.

Every provider speaks the same small protocol so the run engine never branches
on provider identity. Messages use a provider-neutral block format:

    {"role": "user"|"assistant"|"tool", "content": [block, ...]}

    text block   : {"type": "text", "text": str}
    tool call    : {"type": "tool_use", "id": str, "name": str, "input": dict}
    tool result  : {"type": "tool_result", "tool_use_id": str, "content": str}

Each adapter translates this to/from its own wire format. `stream()` yields
`StreamEvent`s; the engine consumes them identically regardless of provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol


@dataclass
class Capabilities:
    context_window: int = 200_000
    supports_tools: bool = True
    supports_vision: bool = False
    supports_caching: bool = False
    # USD per 1M tokens, for the cost meter.
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0


@dataclass
class StreamEvent:
    """One event from a streamed model turn."""

    type: str  # "text" | "tool_use" | "usage" | "done" | "error"
    text: str = ""
    tool_call: dict | None = None  # {"id","name","input"}
    usage: dict = field(default_factory=dict)  # {"input","output"}
    error: str = ""
    stop_reason: str = ""


class Provider(Protocol):
    name: str

    def capabilities(self, model: str) -> Capabilities: ...

    def stream(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        cache: bool = True,
    ) -> Iterator[StreamEvent]: ...

    def count_tokens(self, text: str) -> int: ...


def estimate_tokens(text: str) -> int:
    """Cheap, provider-agnostic token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


# ── Transient-failure retry (rate limits / 5xx / network blips) ──────────────

def is_retryable(e: Exception) -> bool:
    """True for transient provider errors worth retrying (429, 5xx, overloaded,
    connection/timeout) — not for auth/4xx errors, which won't get better."""
    name = type(e).__name__.lower()
    if any(k in name for k in ("ratelimit", "overloaded", "timeout",
                               "apiconnection", "connectionerror", "serviceunavailable",
                               "internalserver")):
        return True
    code = getattr(e, "status_code", None)
    if code is None:
        code = getattr(getattr(e, "response", None), "status_code", None)
    return code in (429, 500, 502, 503, 504)


def retry_sleep(attempt: int, base: float = 1.0, cap: float = 20.0) -> float:
    """Exponential backoff with jitter for retry attempt `attempt` (0-based)."""
    import random
    return min(cap, base * (2 ** attempt)) + random.random()
