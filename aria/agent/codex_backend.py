"""
agent/codex_backend.py - Minimal client for the ChatGPT "Codex" backend.

When the user signs in with their ChatGPT subscription (Codex OAuth), requests
do NOT go to api.openai.com and are NOT the chat.completions format. They go to
    https://chatgpt.com/backend-api/codex/responses
which speaks the OpenAI *Responses* API and is guarded by Cloudflare, which
rejects clients that don't send the Codex first-party headers (you get an HTML
challenge / 403 otherwise — exactly the error a naive SDK call produces).

This module hand-builds that request with the required headers, converts ARIA's
internal message/tool format to the Responses shape, parses the SSE stream, and
runs the same tool-call loop the rest of the orchestrator uses.

HONEST STATUS: this targets an undocumented, community-reverse-engineered
endpoint. The header set and body shape below match what the open-source Codex
CLI sends as of mid-2026, but OpenAI can change it at any time. Treat it as
best-effort.
"""

import json
from typing import Callable

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from agent import openai_oauth

RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"

# Codex first-party identity. "originator" must be on the backend whitelist
# (codex_cli_rs / codex_vscode / codex_sdk_ts / anything starting with "Codex")
# or Cloudflare returns 403. See research notes in the PR description.
ORIGINATOR = "codex_cli_rs"
USER_AGENT = "codex_cli_rs/0.77.0 (ARIA; desktop assistant)"

# The Codex backend requires an "instructions" preamble or it rejects the call.
_DEFAULT_INSTRUCTIONS = "You are a helpful assistant."

# The ChatGPT-account Codex backend rejects legacy ids (gpt-4o, etc.) with
# "The 'gpt-4o' model is not supported when using Codex with a ChatGPT account."
# Fall back to gpt-5.5, which is confirmed working against the live backend.
DEFAULT_CODEX_MODEL = "gpt-5.5"


def _resolve_model(model: str) -> str:
    """Map the configured model to one the Codex backend accepts. The backend
    rejects legacy ids (gpt-4o, gpt-4-turbo, gpt-3.5-...) but accepts the
    current gpt-5.x family / *-codex variants, so pass those through and only
    rewrite the legacy ones."""
    if model and (model.startswith("gpt-5") or model.endswith("-codex")):
        return model
    return DEFAULT_CODEX_MODEL


def _headers(access_token: str, account_id: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": ORIGINATOR,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "session_id": "",  # populated per-call below
    }


def _to_responses_input(messages: list) -> list:
    """Convert ARIA's message list (OpenAI-chat-ish) into Responses 'input'
    items. Each item is {type:'message', role, content:[{type, text}]}."""
    items = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            text = content
        else:
            # Flatten any block list to text; tool blocks are handled via the
            # function-call items below, not here.
            parts = []
            for b in content:
                if isinstance(b, dict):
                    parts.append(b.get("text", "") or "")
                else:
                    parts.append(str(b))
            text = "\n".join(parts)
        # Responses uses 'input_text' for user/system and 'output_text' for
        # prior assistant turns.
        ctype = "output_text" if role == "assistant" else "input_text"
        items.append({"type": "message", "role": role,
                      "content": [{"type": ctype, "text": text}]})
    return items


def _to_responses_tools(schemas: list) -> list:
    """Responses API tool shape is flatter than chat.completions: the function
    fields live at the top level of each tool object."""
    tools = []
    for s in schemas:
        tools.append({
            "type": "function",
            "name": s["name"],
            "description": s.get("description", ""),
            "parameters": s["input_schema"],
        })
    return tools


def _parse_sse(resp, on_token: Callable) -> dict:
    """Read the SSE stream, forwarding text deltas to on_token. Returns a dict
    with 'text' (full output) and 'tool_calls' (list of {id,name,arguments})."""
    text_parts = []
    tool_calls = {}  # call_id -> {name, arguments}

    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        # iter_lines can still yield bytes when the response has no charset;
        # normalize to str before any string ops.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not raw.startswith("data:"):
            continue
        data = raw[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            evt = json.loads(data)
        except json.JSONDecodeError:
            continue

        etype = evt.get("type", "")
        # Streaming text deltas.
        if etype == "response.output_text.delta":
            delta = evt.get("delta", "")
            if delta:
                text_parts.append(delta)
                on_token(delta)
        # A function/tool call was emitted as an output item.
        elif etype == "response.output_item.done":
            item = evt.get("item", {})
            if item.get("type") == "function_call":
                tool_calls[item.get("call_id", item.get("id", ""))] = {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "{}"),
                }
        # Some server builds only send the final aggregated response.
        elif etype == "response.completed":
            resp_obj = evt.get("response", {})
            for item in resp_obj.get("output", []):
                if item.get("type") == "function_call":
                    tool_calls.setdefault(
                        item.get("call_id", item.get("id", "")),
                        {"name": item.get("name", ""),
                         "arguments": item.get("arguments", "{}")})

    return {"text": "".join(text_parts), "tool_calls": tool_calls}


def run(orchestrator, messages, system_prompt, all_tools, schemas, model, max_iters):
    """Drive the Codex Responses endpoint through the agentic tool loop, using
    the orchestrator's callbacks. `orchestrator` provides on_token/on_tool_call/
    on_tool_result/on_done/on_error, _call_tool, and the _stop flag."""
    if not REQUESTS_AVAILABLE:
        orchestrator.on_error("requests not installed. Run: pip install requests")
        return

    token = openai_oauth.get_access_token()
    account_id = openai_oauth.get_account_id()
    if not token:
        orchestrator.on_error("Not signed in with ChatGPT. Go to Settings → Sign in.")
        return
    if not account_id:
        orchestrator.on_error(
            "Couldn't read your ChatGPT account id from the sign-in token. "
            "Try signing out and back in.")
        return

    headers = _headers(token, account_id)
    import uuid
    headers["session_id"] = str(uuid.uuid4())

    convo = _to_responses_input(messages)
    tools = _to_responses_tools(schemas)
    model = _resolve_model(model)  # Codex backend only accepts gpt-*-codex ids

    for _ in range(max_iters):
        if orchestrator._stop:
            orchestrator.on_done("")
            return

        body = {
            "model": model,
            "instructions": system_prompt or _DEFAULT_INSTRUCTIONS,
            "input": convo,
            "tools": tools,
            "stream": True,
            "store": False,
        }

        try:
            resp = requests.post(RESPONSES_URL, headers=headers,
                                 json=body, stream=True, timeout=180)
        except Exception as e:
            orchestrator.on_error(f"Codex request failed: {e}")
            return

        if resp.status_code != 200:
            snippet = resp.text[:200].replace("\n", " ")
            if resp.status_code in (401, 403):
                orchestrator.on_error(
                    f"Codex backend rejected the request ({resp.status_code}). "
                    "Your ChatGPT plan may not allow API-style access, or the "
                    f"token expired. Details: {snippet}")
            else:
                orchestrator.on_error(f"Codex backend error {resp.status_code}: {snippet}")
            return

        result = _parse_sse(resp, orchestrator.on_token)
        tool_calls = result["tool_calls"]

        if not tool_calls:
            orchestrator.on_done(result["text"])
            return

        # Echo the assistant's function calls back into the conversation, then
        # append each tool's output, and loop for the model's follow-up.
        for call_id, call in tool_calls.items():
            try:
                args = json.loads(call["arguments"]) if call["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            orchestrator.on_tool_call(call["name"], args)
            tool_result = orchestrator._call_tool(call["name"], args, all_tools)
            orchestrator.on_tool_result(call["name"], tool_result)

            convo.append({"type": "function_call", "call_id": call_id,
                          "name": call["name"], "arguments": call["arguments"]})
            convo.append({"type": "function_call_output", "call_id": call_id,
                          "output": json.dumps(tool_result)})

    orchestrator.on_error(f"Agent reached max iterations ({max_iters}). Task too complex.")
