"""
agent/orchestrator.py - The brain of ARIA.

Handles the agentic loop: sends messages to the AI, processes tool calls,
feeds results back, and streams progress to the GUI callback.
Supports Claude, OpenAI, and local Ollama. Includes all tool sets.
"""

import json
import threading
from typing import Callable, Optional

from config import settings as cfg
from agent.file_tools import TOOLS as FILE_TOOLS, TOOL_SCHEMAS as FILE_SCHEMAS
from agent.computer_tools import COMPUTER_TOOLS, COMPUTER_TOOL_SCHEMAS, take_screenshot
from agent.browser_tools import BROWSER_TOOLS, BROWSER_TOOL_SCHEMAS
from agent.search_tools import SEARCH_TOOLS, SEARCH_TOOL_SCHEMAS
from agent.memory import MEMORY_TOOLS, MEMORY_TOOL_SCHEMAS, get_memory_summary
from agent.messaging_tools import MESSAGING_TOOLS, MESSAGING_TOOL_SCHEMAS
from agent.plugins import load_plugins

try:
    import anthropic

    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    import openai

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

MAX_ITERATIONS = 30

# Load plugins once at startup
_plugin_tools, _plugin_schemas = load_plugins()


def _build_tool_registry(
    use_computer: bool, use_browser: bool, advanced: bool = False
) -> tuple[dict, list]:
    """Build the complete tool registry for an agent run."""
    tools = {}
    schemas = []

    # Always available
    tools.update(FILE_TOOLS)
    tools.update(SEARCH_TOOLS)
    tools.update(MEMORY_TOOLS)
    tools.update(MESSAGING_TOOLS)
    tools.update(_plugin_tools)
    schemas += (
        FILE_SCHEMAS
        + SEARCH_TOOL_SCHEMAS
        + MEMORY_TOOL_SCHEMAS
        + MESSAGING_TOOL_SCHEMAS
        + _plugin_schemas
    )

    if use_computer:
        tools.update(COMPUTER_TOOLS)
        schemas += COMPUTER_TOOL_SCHEMAS

    if use_browser:
        tools.update(BROWSER_TOOLS)
        schemas += BROWSER_TOOL_SCHEMAS

    # Advanced mode: multi-agent orchestration + planning + code runner.
    if advanced:
        from agent.orchestration import (
            ORCHESTRATION_TOOLS,
            ORCHESTRATION_TOOL_SCHEMAS,
        )
        from agent.planning import PLANNING_TOOLS, PLANNING_TOOL_SCHEMAS
        from agent.code_runner import CODE_RUNNER_TOOLS, CODE_RUNNER_TOOL_SCHEMAS

        tools.update(ORCHESTRATION_TOOLS)
        tools.update(PLANNING_TOOLS)
        tools.update(CODE_RUNNER_TOOLS)
        schemas += (
            ORCHESTRATION_TOOL_SCHEMAS
            + PLANNING_TOOL_SCHEMAS
            + CODE_RUNNER_TOOL_SCHEMAS
        )

    return tools, schemas


class AgentOrchestrator:
    def __init__(
        self,
        on_token: Callable[[str], None],
        on_tool_call: Callable[[str, dict], None],
        on_tool_result: Callable[[str, dict], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
    ):
        self.on_token = on_token
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_done = on_done
        self.on_error = on_error
        self._stop = False

    def stop(self):
        self._stop = True

    def run(
        self,
        messages: list,
        system_prompt: str,
        use_computer_tools: bool = False,
        use_browser_tools: bool = True,
        include_screenshot: bool = False,
        overrides: dict = None,
    ):
        s = cfg.load()
        # Per-chat / per-task AI selection: patch the settings dict with any
        # provider/model overrides so the chosen AI is used for this run only.
        if overrides:
            s = {**s, **{k: v for k, v in overrides.items() if v}}
        provider = s.get("provider", "claude")

        # Inject memory into system prompt
        memory_text = get_memory_summary()
        if memory_text:
            system_prompt = f"{system_prompt}\n\n{memory_text}"

        # If the active project has a working folder, tell the agent so it works
        # there by default (like a Claude Code project directory).
        proj_folder = cfg.active_project_folder()
        if proj_folder:
            system_prompt += (
                f"\n\nThe current project's working folder is: {proj_folder}\n"
                "Use this as the base directory for file operations and commands "
                "unless the user specifies another path."
            )

        # Reply-language directive.
        lang = s.get("response_language", "auto")
        if lang == "sv":
            system_prompt += "\n\nAlways respond in Swedish (svenska)."
        elif lang == "en":
            system_prompt += "\n\nAlways respond in English."
        elif lang == "auto":
            system_prompt += "\n\nRespond in the same language the user writes in."

        # Advanced mode: tell the agent it can plan and coordinate other agents.
        if s.get("advanced_mode", False):
            system_prompt += (
                "\n\nADVANCED MODE: For complex, multi-step jobs, first call "
                "create_plan with the steps you intend to take, then update_plan "
                "('doing'/'done') as you progress so the user can follow along. You "
                "can also orchestrate other specialist agents: use list_agents to "
                "see who's available and delegate_to_agent to assign a self-contained "
                "sub-task to the best-suited agent (e.g. research, writing, file "
                "work), then combine their results. Do simple tasks yourself."
            )

        try:
            if provider == "claude":
                self._run_claude(
                    messages,
                    system_prompt,
                    use_computer_tools,
                    use_browser_tools,
                    include_screenshot,
                    s,
                )
            elif provider == "openai":
                self._run_openai(
                    messages, system_prompt, use_computer_tools, use_browser_tools, s
                )
            elif provider == "local":
                self._run_ollama(messages, system_prompt, s)
            else:
                self.on_error(f"Unknown provider: {provider}")
        except Exception as e:
            self.on_error(str(e))

    # ── Claude ─────────────────────────────────────────────────────────────

    def _run_claude(
        self, messages, system_prompt, use_computer, use_browser, include_screenshot, s
    ):
        if not ANTHROPIC_AVAILABLE:
            self.on_error("anthropic package not installed. Run: pip install anthropic")
            return
        api_key = s.get("claude_api_key", "")
        if not api_key:
            self.on_error(
                "No Claude API key. Go to Settings → add your Claude API key."
            )
            return

        client = anthropic.Anthropic(api_key=api_key)
        model = s.get("claude_model", "claude-opus-4-5")
        all_tools, schemas = _build_tool_registry(
            use_computer, use_browser, advanced=s.get("advanced_mode", False)
        )

        if include_screenshot:
            ss = take_screenshot()
            if "image_base64" in ss:
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": ss["image_base64"],
                                },
                            },
                            {"type": "text", "text": "Current screen state:"},
                        ],
                    }
                ] + messages

        for iteration in range(MAX_ITERATIONS):
            if self._stop:
                self.on_done("")
                return

            response = client.messages.create(
                model=model,
                max_tokens=s.get("max_tokens", 4096),
                system=system_prompt,
                tools=schemas,
                messages=messages,
            )

            full_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    full_text += block.text
                    self.on_token(block.text)

            if response.stop_reason == "end_turn":
                self.on_done(full_text)
                return

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    self.on_tool_call(block.name, block.input)
                    result = self._call_tool(block.name, block.input, all_tools)
                    self.on_tool_result(block.name, result)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
                continue

            self.on_done(full_text)
            return

        self.on_error(
            f"Agent reached max iterations ({MAX_ITERATIONS}). Task too complex."
        )

    # ── OpenAI ─────────────────────────────────────────────────────────────

    def _run_openai(self, messages, system_prompt, use_computer, use_browser, s):
        all_tools, schemas = _build_tool_registry(use_computer, use_browser)
        model = s.get("openai_model", "gpt-4o")

        # "Sign in with ChatGPT" (Codex OAuth) does NOT use the OpenAI SDK or
        # api.openai.com — it speaks the Responses API at the Cloudflare-guarded
        # ChatGPT backend, which needs Codex-specific headers. Delegate to the
        # dedicated client; it runs its own tool loop via this orchestrator.
        if s.get("openai_auth_mode") == "oauth":
            from agent import codex_backend

            codex_backend.run(
                self, messages, system_prompt, all_tools, schemas, model, MAX_ITERATIONS
            )
            return

        if not OPENAI_AVAILABLE:
            self.on_error("openai not installed. Run: pip install openai")
            return
        api_key = s.get("openai_api_key", "")
        if not api_key:
            self.on_error("No OpenAI API key. Go to Settings.")
            return
        client = openai.OpenAI(api_key=api_key)

        def to_oai(schema):
            return {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["input_schema"],
                },
            }

        oai_messages = [{"role": "system", "content": system_prompt}] + messages

        for _ in range(MAX_ITERATIONS):
            if self._stop:
                self.on_done("")
                return
            response = client.chat.completions.create(
                model=model,
                messages=oai_messages,
                tools=[to_oai(t) for t in schemas],
                max_tokens=s.get("max_tokens", 4096),
            )
            msg = response.choices[0].message
            text = msg.content or ""
            if text:
                self.on_token(text)
            if not msg.tool_calls:
                self.on_done(text)
                return
            oai_messages.append(msg)
            for tc in msg.tool_calls:
                try:
                    inp = json.loads(tc.function.arguments)
                except Exception:
                    inp = {}
                self.on_tool_call(tc.function.name, inp)
                result = self._call_tool(tc.function.name, inp, all_tools)
                self.on_tool_result(tc.function.name, result)
                oai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )

        self.on_error(f"Max iterations reached.")

    # ── Ollama ─────────────────────────────────────────────────────────────

    def _run_ollama(self, messages, system_prompt, s):
        if not REQUESTS_AVAILABLE:
            self.on_error("requests not installed")
            return
        url = s.get("ollama_url", "http://localhost:11434")
        model = s.get("ollama_model", "llama3")
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}]
            + [
                {
                    "role": m["role"],
                    "content": (
                        m["content"]
                        if isinstance(m["content"], str)
                        else str(m["content"])
                    ),
                }
                for m in messages
            ],
            "stream": False,
        }
        try:
            resp = requests.post(f"{url}/api/chat", json=payload, timeout=120)
            resp.raise_for_status()
            text = resp.json().get("message", {}).get("content", "")
            self.on_token(text)
            self.on_done(text)
        except requests.exceptions.ConnectionError:
            self.on_error(f"Cannot connect to Ollama at {url}. Is Ollama running?")
        except Exception as e:
            self.on_error(str(e))

    # ── Tool executor ──────────────────────────────────────────────────────

    def _call_tool(self, name: str, inp: dict, tools: dict) -> dict:
        func = tools.get(name)
        if not func:
            return {"error": f"Unknown tool: {name}"}
        try:
            if name == "keyboard_hotkey" and "keys" in inp:
                return func(*inp["keys"])
            return func(**inp)
        except Exception as e:
            return {"error": f"Tool failed: {e}"}


def run_agent_in_thread(
    messages: list,
    system_prompt: str,
    on_token: Callable,
    on_tool_call: Callable,
    on_tool_result: Callable,
    on_done: Callable,
    on_error: Callable,
    use_computer_tools: bool = False,
    use_browser_tools: bool = True,
    include_screenshot: bool = False,
    overrides: dict = None,
) -> AgentOrchestrator:
    orch = AgentOrchestrator(on_token, on_tool_call, on_tool_result, on_done, on_error)
    t = threading.Thread(
        target=orch.run,
        args=(
            messages,
            system_prompt,
            use_computer_tools,
            use_browser_tools,
            include_screenshot,
            overrides,
        ),
        daemon=True,
    )
    t.start()
    return orch


def run_agent_sync(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    use_computer_tools: bool = True,
    use_browser_tools: bool = True,
    overrides: dict = None,
) -> str:
    """Run the agent to completion on a single prompt and return the reply text.
    Used by the messaging service (which is already on a background thread).
    Collects streamed tokens and the final/erro r result into one string."""
    collected = {"text": "", "error": None}

    def on_token(t):
        collected["text"] += t

    def on_done(text):
        # Prefer the streamed text; fall back to the final text if empty.
        if not collected["text"]:
            collected["text"] = text or ""

    def on_error(e):
        collected["error"] = str(e)

    orch = AgentOrchestrator(
        on_token, lambda *a: None, lambda *a: None, on_done, on_error
    )
    orch.run(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system_prompt,
        use_computer_tools=use_computer_tools,
        use_browser_tools=use_browser_tools,
        include_screenshot=False,
        overrides=overrides,
    )
    if collected["error"] and not collected["text"]:
        return f"Error: {collected['error']}"
    return collected["text"].strip()
