"""core/config.py - Isolated app configuration for aria-v2.

Deliberately separate from v1. State lives under %APPDATA%/ARIA2 (or
~/.config/ARIA2). This file holds ONLY genuine app preferences and secrets;
all entity data (chats, agents, runs, memory, …) lives in the SQLite DB.

Settings here are small and read rarely, so a JSON file is the right tool —
the mistake in v1 was putting *entities* in JSON, not preferences.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from aria2.core import secrets as _secrets

_APP_DIR_NAME = "ARIA2"


def app_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / _APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_FILE = app_dir() / "config.json"
DB_FILE = app_dir() / "aria2.db"
KNOWLEDGE_DIR = app_dir() / "knowledge"

DEFAULTS: dict = {
    # ── Providers ────────────────────────────────────────────────────────────
    "provider": "claude",
    "claude_model": "claude-opus-4-8",
    "claude_api_key": "",
    "openai_model": "gpt-4o",
    "openai_api_key": "",
    # OpenAI auth: "apikey" or "oauth" (Sign in with OpenAI). OAuth tokens +
    # endpoints are stored here; access/refresh tokens are encrypted at rest.
    "openai_auth_mode": "apikey",
    "openai_oauth_token": "",
    "openai_oauth_refresh": "",
    "openai_oauth_expires": 0,
    "openai_oauth_client_id": "",
    "openai_oauth_auth_url": "",
    "openai_oauth_token_url": "",
    "openai_oauth_scope": "",
    "ollama_model": "llama3",
    "ollama_url": "http://localhost:11434",
    # Context window ARIA will pack for local/OpenAI-compatible models. Must not
    # exceed the model's actual num_ctx in Ollama or Ollama silently truncates —
    # tune to match your setup (raise for llama3.1/qwen2.5 large-context models).
    "ollama_num_ctx": 8192,
    # Tool-calling policy for local/OpenAI-compatible models:
    #   "auto"   = detect per known model (safe default),
    #   "always" = force-enable (e.g. vLLM/LM Studio serving a capable model),
    #   "never"  = disable tools, converse only (most reliable on weak models).
    "ollama_tool_mode": "auto",
    # Generic OpenAI-compatible endpoint (LM Studio / vLLM / llama.cpp / LocalAI /
    # KoboldCpp / Text-Generation-WebUI / OpenRouter / any /v1 chat server).
    # base_url accepts a bare host (http://localhost:1234) — /v1 is appended.
    "oai_compat_base_url": "http://localhost:1234/v1",
    "oai_compat_api_key": "",        # required for OpenRouter; blank for local
    "oai_compat_model": "",
    "oai_compat_num_ctx": 8192,
    "oai_compat_tool_mode": "auto",  # auto/always = tools on; never = converse only
    # Gemini (Google) — OpenAI-compatible endpoint; API key from AI Studio.
    "gemini_model": "gemini-2.0-flash",
    "gemini_api_key": "",
    # Grok (xAI) — OpenAI-compatible API at api.x.ai. Supports API key or OAuth.
    "grok_model": "grok-2-latest",
    "grok_api_key": "",
    "grok_auth_mode": "apikey",
    "grok_oauth_token": "",
    "grok_oauth_refresh": "",
    "grok_oauth_expires": 0,
    "grok_oauth_client_id": "",
    "grok_oauth_auth_url": "",
    "grok_oauth_token_url": "",
    "grok_oauth_scope": "",
    # Embeddings: "voyage" (Anthropic-recommended), "openai", "ollama" (real local
    # semantic vectors, free/offline — needs the model pulled), or "local" (offline
    # hashing fallback — works with no key, lower quality).
    "embedding_provider": "local",
    "ollama_embed_model": "nomic-embed-text",
    "voyage_api_key": "",
    "ollama_idle_unload_min": 10,   # unload idle local models after N minutes
    # ── Generation ─────────────────────────────────────────────────────────
    "max_tokens": 4096,
    "temperature": 1.0,
    "prompt_caching": True,
    # ── Engine ────────────────────────────────────────────────────────────
    # Max top-level runs executing at once (chat/trigger/messaging/fork). Excess
    # work queues rather than spawning unbounded threads. Delegated sub-agents
    # use a separate pool, so this doesn't throttle a single agent's fan-out.
    "max_concurrent_runs": 8,
    # Project Leader (/team): how many independent tasks run in parallel, and
    # whether code tasks get an automatic reviewer pass before acceptance.
    "orchestration_max_parallel": 3,
    "auto_review": True,
    # Stage 3: how many revise→re-run rounds a code task may take when the
    # reviewer or a deliverable contract rejects it; and whether the leader pauses
    # for human approval (`/team go`) after planning before it executes.
    "max_revisions": 2,
    "orchestration_plan_approval": False,
    "max_iterations": 40,
    "context_token_budget": 120_000,
    "default_run_budget_usd": 1.0,
    # Context compiler: let the engine route trivial/heavy tasks to cheaper/
    # stronger models within the active provider (model-neutral edge).
    "auto_route": False,
    # Ambient capture: passively observe project folders to mine reusable
    # automations. Off by default — opt-in for privacy.
    "ambient_enabled": False,
    # Delegation: let an agent spawn worker sub-agents (in parallel) as durable
    # child runs, with routing learned from past performance.
    "delegation_enabled": True,
    "max_delegation_depth": 2,
    # MCP connectors: expose external tool servers in the tool registry.
    "mcp_enabled": True,
    # Self-improvement: on a failed run, diff vs. successes and propose a fix.
    "self_improvement_enabled": False,
    # Webhook triggers: a localhost HTTP listener that fires triggers on request.
    "webhook_enabled": False,
    "webhook_port": 8765,
    # Auto-update: poll a JSON manifest {version, url, notes} and offer the update.
    "auto_check_updates": True,
    "update_manifest_url": "https://github.com/zeequxz/aria-desktop-assistant/releases/latest/download/latest.json",
    # ── UI ────────────────────────────────────────────────────────────────
    "theme": "dark",
    "accent": "#6c8fff",
    "font_size": 13,
    # Window geometry (restored on next launch).
    "window_x": None,
    "window_y": None,
    "window_width": 1240,
    "window_height": 820,
    "sidebar_nav_width": 216,       # main nav rail
    "sidebar_chat_width": 256,      # chat/project panel
    "sidebar_agents_width": 240,
    "sidebar_memory_width": 320,
    "sidebar_connectors_width": 240,
    "sidebar_runs_width": 380,
    "sidebar_projects_width": 220,
    "sidebar_automations_width": 380,
    "sidebar_evals_width": 420,
    "sidebar_knowledge_width": 480,
    "active_project": "general",
    "active_agent": "assistant",
    # ── Safety ──────────────────────────────────────────────────────────────
    # Global default permission for tools not explicitly scoped: ask | allow | deny
    "default_tool_policy": "ask",
    # Computer use: allow mouse/keyboard/screen tools (high risk; default off).
    "computer_use_enabled": False,
    # Where run_shell executes:  host = directly (working dir pinned, default) |
    # docker = throwaway container (only the project dir mounted, no network) |
    # wsl = the WSL Linux subsystem. docker/wsl give real isolation but must be
    # installed. Destructive commands always require approval regardless.
    "exec_backend": "host",
    "exec_docker_image": "python:3.12-slim",
    # ── Messaging (Telegram bridge) ─────────────────────────────────────────
    "messaging_enabled": False,
    "telegram_bot_token": "",
    "telegram_allowlist": [],          # chat IDs permitted to command the bot
    # Access level for messaging-driven runs: full | restricted | chat_only
    "messaging_access": "restricted",
    "messaging_project": "general",
    "messaging_agent": "assistant",
    # Which AI model handles incoming Telegram/Discord messages.
    # "default" = use the global provider from Settings.
    # "local"   = always use Ollama (private, fast, offline).
    # "cloud"   = always use the configured cloud provider (smarter).
    "messaging_provider": "default",
    # When True, even "full" access routes shell + PC-control tools through the
    # host approval dialog (human-in-the-loop) rather than auto-allowing them.
    "messaging_require_confirmation": True,
    # When a messaging run is pinned to the local model and it errors/unavailable,
    # retry the turn transparently with the configured cloud provider so the user
    # still gets a reply (graceful degradation). Only applies when provider=local.
    "messaging_fallback_to_cloud": True,
    # On startup, skip Telegram messages that queued while the bot was offline,
    # so a backlog can't trigger a flood of stale agent runs. Set False to
    # process the backlog (the older behaviour).
    "telegram_drain_backlog": True,
    # Discord output: a default webhook + named channel webhooks for topic routing.
    "discord_webhook_url": "",
    "discord_channels": [],            # [{"name": ..., "url": ...}]
    # Discord inbound (a gateway bot; needs discord.py). Off by default.
    "discord_inbound_enabled": False,
    "discord_bot_token": "",
    "discord_allowlist": [],           # Discord user IDs permitted to command
    # ── v1 extras ────────────────────────────────────────────────────────────
    "browser_enabled": True,           # fetch_url / web_search / open_url tools
    "tts_enabled": False,              # speak replies aloud (pyttsx3)
    "tts_rate": 175,
    "tts_voice": "",
    "tray_enabled": False,             # system tray icon + minimize-to-tray
    "heartbeat_enabled": False,        # proactive periodic check-in
    "heartbeat_interval": 30,          # minutes
    "heartbeat_prompt": "",
    "heartbeat_agent": "assistant",
    "heartbeat_project": "general",
}

_lock = threading.RLock()
_cache: dict | None = None


def load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        data = dict(DEFAULTS)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                saved = _secrets.decrypt_settings(saved)
                data.update(saved)
            except Exception:
                pass
        _cache = data
        return dict(data)


def save(settings: dict) -> None:
    global _cache
    with _lock:
        _cache = dict(settings)
        to_write = _secrets.encrypt_settings(settings)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(to_write, f, indent=2, ensure_ascii=False)
        tmp.replace(CONFIG_FILE)  # atomic on the same volume


def get(key: str, default=None):
    return load().get(key, default)


def provider_configured(settings: dict | None = None) -> bool:
    """True if the active provider has credentials (or is local Ollama).
    Used by onboarding to decide whether to nudge the user to Settings."""
    s = settings or load()
    p = s.get("provider", "claude")
    if p == "local":
        return True
    if p == "claude":
        return bool(s.get("claude_api_key"))
    if p == "openai":
        return bool(s.get("openai_api_key") or s.get("openai_oauth_token"))
    if p == "grok":
        return bool(s.get("grok_api_key") or s.get("grok_oauth_token"))
    if p == "gemini":
        return bool(s.get("gemini_api_key"))
    if p == "openai_compat":
        # Local servers need no key; only a base URL + model are required.
        return bool(s.get("oai_compat_base_url") and s.get("oai_compat_model"))
    return False


def set_key(key: str, value) -> None:
    with _lock:
        s = load()
        s[key] = value
        save(s)
