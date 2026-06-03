"""models/codex_provider.py - ChatGPT subscription provider (Responses API).

When the user signs in with "Sign in with OpenAI", requests go to
  https://chatgpt.com/backend-api/codex/responses
NOT to api.openai.com. This endpoint speaks the OpenAI *Responses* API shape
and requires Codex first-party headers (originator, chatgpt-account-id) or
Cloudflare returns 403. Ported from v1's codex_backend.py.

HONEST CAVEATS: community-reverse-engineered, undocumented endpoint. Works as
of mid-2026 with the public constants from the open-source Codex CLI. OpenAI
can change it at any time.
"""

from __future__ import annotations

import json
import uuid
from typing import Iterator

from aria2.models.base import Capabilities, StreamEvent, estimate_tokens

try:
    import requests as _requests
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
ORIGINATOR = "codex_cli_rs"
USER_AGENT = "codex_cli_rs/0.77.0 (ARIA; desktop assistant)"
DEFAULT_MODEL = "gpt-5.5"

_PRICING = {
    "gpt-5.5": (5.0, 20.0),
    "gpt-4o": (2.5, 10.0),
}


class CodexProvider:
    """Drives the ChatGPT subscription Codex backend (no API key needed)."""

    name = "codex"

    def __init__(self, access_token: str, account_id: str):
        if not AVAILABLE:
            raise RuntimeError("requests not installed (pip install requests)")
        if not access_token:
            raise RuntimeError(
                "Not signed in. Go to Settings → Sign in with OpenAI.")
        self._token = access_token
        self._account_id = account_id or ""

    def capabilities(self, model: str) -> Capabilities:
        cin, cout = _PRICING.get(model, (5.0, 20.0))
        return Capabilities(
            context_window=128_000,
            supports_tools=True,
            supports_vision=False,
            supports_caching=False,
            input_cost_per_mtok=cin,
            output_cost_per_mtok=cout,
        )

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "chatgpt-account-id": self._account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": ORIGINATOR,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "session_id": str(uuid.uuid4()),
        }

    @staticmethod
    def _to_input(system: str, messages: list[dict]) -> tuple[str, list]:
        """Convert neutral messages to Responses API input items.
        Returns (instructions, input_items)."""
        items = []
        for m in messages:
            role = m.get("role", "user")
            if role == "tool":
                # Tool results become function_call_output items.
                for b in (m.get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        items.append({
                            "type": "function_call_output",
                            "call_id": b.get("tool_use_id", ""),
                            "output": b.get("content", ""),
                        })
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                text = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                # Tool-use blocks in assistant messages become function_call items.
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        try:
                            args = json.dumps(b.get("input", {}))
                        except Exception:
                            args = "{}"
                        items.append({
                            "type": "function_call",
                            "call_id": b.get("id", ""),
                            "name": b.get("name", ""),
                            "arguments": args,
                        })
            else:
                text = str(content)
            ctype = "output_text" if role == "assistant" else "input_text"
            if text:
                items.append({
                    "type": "message", "role": role,
                    "content": [{"type": ctype, "text": text}],
                })
        return system, items

    @staticmethod
    def _to_tools(tool_schemas: list[dict]) -> list:
        return [
            {"type": "function", "name": s["name"],
             "description": s.get("description", ""),
             "parameters": s.get("input_schema", {})}
            for s in (tool_schemas or [])
        ]

    @staticmethod
    def _resolve_model(model: str) -> str:
        if model and (model.startswith("gpt-5") or model.endswith("-codex")):
            return model
        return DEFAULT_MODEL

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
        instructions, input_items = self._to_input(system, messages)
        body = {
            "model": self._resolve_model(model),
            "instructions": instructions,
            "input": input_items,
            "tools": self._to_tools(tools),
            "stream": True,
            "store": False,
        }
        try:
            resp = _requests.post(
                RESPONSES_URL, headers=self._headers(),
                json=body, stream=True, timeout=180)
        except Exception as e:
            yield StreamEvent(type="error", error=f"Codex request failed: {e}")
            return

        if resp.status_code != 200:
            snippet = resp.text[:300].replace("\n", " ")
            if resp.status_code in (401, 403):
                yield StreamEvent(
                    type="error",
                    error=f"ChatGPT rejected the request ({resp.status_code}). "
                          "Your plan may not include Codex access, or the token expired. "
                          f"Try signing out and back in. Details: {snippet}")
            else:
                yield StreamEvent(
                    type="error", error=f"Codex error {resp.status_code}: {snippet}")
            return

        tool_calls: dict[str, dict] = {}
        stop_reason = "end_turn"

        for raw in resp.iter_lines(decode_unicode=True):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type", "")
            if etype == "response.output_text.delta":
                delta = evt.get("delta", "")
                if delta:
                    yield StreamEvent(type="text", text=delta)
            elif etype == "response.output_item.done":
                item = evt.get("item", {})
                if item.get("type") == "function_call":
                    cid = item.get("call_id") or item.get("id", "")
                    tool_calls[cid] = {
                        "id": cid,
                        "name": item.get("name", ""),
                        "input": self._parse_args(item.get("arguments", "{}")),
                    }
                    stop_reason = "tool_use"
            elif etype == "response.completed":
                for item in evt.get("response", {}).get("output", []):
                    if item.get("type") == "function_call":
                        cid = item.get("call_id") or item.get("id", "")
                        tool_calls.setdefault(cid, {
                            "id": cid,
                            "name": item.get("name", ""),
                            "input": self._parse_args(item.get("arguments", "{}")),
                        })
                        stop_reason = "tool_use"

        for cid, tc in tool_calls.items():
            yield StreamEvent(type="tool_use", tool_call=tc)
        yield StreamEvent(type="usage", usage={"input": 0, "output": 0})
        yield StreamEvent(type="done", stop_reason=stop_reason)

    @staticmethod
    def _parse_args(args_str: str) -> dict:
        try:
            return json.loads(args_str) if args_str else {}
        except Exception:
            return {}
