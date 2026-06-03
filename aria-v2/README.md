# ARIA v2 — Local-first AI Workstation

Providers: **Claude**, **OpenAI** (API key or OAuth), **Grok / xAI** (API key or
OAuth), **Gemini / Google** (API key), and local **Ollama** — switchable globally
or per-agent.


A ground-up rebuild of ARIA around a real substrate. Where v1 stored every
entity in one JSON file, injected all memory wholesale into the prompt, and ran
a non-streaming agent loop, v2 is built on:

- **SQLite (WAL)** data layer — chats, messages (as trees, for branching), runs,
  steps, agents, triggers, memory, knowledge, and audit are all rows.
- **A model abstraction** with streaming + Anthropic **prompt caching**, plus
  OpenAI and local Ollama adapters behind one interface.
- **A durable run engine** — every chat turn, scheduled task, and delegation is
  an inspectable *run* with steps, token/cost accounting, budgets, and cancel.
- **Retrieval memory** — facts are embedded and recalled by relevance × recency
  × importance, not dumped into the prompt.
- **Knowledge / RAG** — ingest files and folders, search with citations.
- **Enforced tool permissions + a sandbox** — "ask/allow/deny" is checked before
  a tool runs (not a prompt instruction), with an approval dialog and audit log.
- **A trigger/scheduler** that persists `next_run` and fires agents with retries.

Everything routes through `services/`, so the GUI, the scheduler, and future
surfaces share one engine.

## Architecture

```
ui/          CustomTkinter desktop client (thin)
services/    application layer — the only thing the UI calls
runtime/     run engine, context engine, tools + permissions + sandbox
models/      provider abstraction (stream / cache / embed)
core/        SQLite data layer, isolated config, event bus, ids
```

The UI never imports `runtime/` or `models/` directly — it subscribes to the
event bus and calls services.

## Run it

```bat
pip install -r requirements.txt
python -m aria2            REM launches the desktop app
python -m aria2 --smoke    REM headless end-to-end checks (no API key needed)
```

State lives under `%APPDATA%\ARIA2\` (config + `aria2.db`), fully isolated from
v1. Add your API keys in **Settings → AI Providers**. With no keys and the
default `local` embedding provider, memory and knowledge still work offline
(lower-quality hashing embeddings); only live model calls need a key.

## The four moats (hard for competitors to copy)

1. **Provenance memory & belief revision** — every fact records the run that
   produced it and the facts it was derived from. You can see a belief's
   derivation, **retract** a wrong fact (which flags everything derived from it
   for review), and **supersede** facts while keeping lineage. Recall never
   returns retracted beliefs. See the **Memory** view. *(Cloud vendors avoid
   storing inferred beliefs about a user — a liability there, an asset here.)*

2. **Time-travel runs** — each model step persists the exact context it saw, so
   any run is reproducible. **Fork from any step** (optionally rewriting the last
   message for a counterfactual) and **diff** two runs in the **Runs** inspector.
   *(A ground-up rewrite for any streaming-session product to retrofit.)*

3. **Ambient capture → automation proposals** — opt-in, local-only. ARIA watches
   your project folders, mines recurring patterns, and **proposes automations**
   you accept with one click (see **Automations**). *(A cloud agent can't legally
   watch your machine, so it can't learn what you actually do.)*

4. **Model-neutral context compiler** — for each turn it compiles the optimal
   context window for whatever model is targeted (budgeted: system > memory >
   knowledge > recent history) and can **auto-route** trivial vs. hard tasks to
   cheaper/stronger models. *(A single-model vendor structurally cannot ship the
   feature whose value is "your model is interchangeable.")*

5. **Self-improving agent org** — a supervisor agent fans out work to specialist
   workers that run as durable, parented **child runs in parallel**
   (`delegate` / `delegate_parallel`). Every delegated run is scored into
   per-agent, per-task-type stats, so a **learned router** (`suggest_agent`)
   sends each kind of work to whoever's actually best — a flywheel that
   compounds with use. See learned performance per agent in the **Agents** view
   and the delegation tree in **Runs**. *(Stateless sub-agents in other products
   have no history to learn from.)*

6. **MCP connectors** — connect external [Model Context Protocol](https://modelcontextprotocol.io)
   servers (stdio) and their tools join ARIA's *same* permission-gated registry,
   audit log, and run inspector. The fastest way to expand capability without
   bespoke plugins. Manage them in the **Connectors** view; a built-in echo test
   server ships under `aria2/devtools/`. *(Synchronous, dependency-free client.)*

7. **Self-improvement from failures** — because a failed run is fully recorded,
   ARIA can diff it against past successes, hypothesise a concrete fix (LLM when
   available, heuristic otherwise), and file an **agent improvement proposal**.
   Accepting it appends versioned guidance to that agent's system prompt. Opt-in.
   *(Stateless products have no run history to learn from.)*

8. **Event-driven automation** — beyond schedules, triggers fire on **file
   changes** (folder watcher) and **webhooks** (a localhost listener at
   `/hook/<id>?token=…`, payload passed as context). Same durable run + retry
   path. Pick the kind in the **Automations** view.

9. **Speculative dry-run sandbox** — toggle **Dry run** in chat and the agent's
   file writes go to a copy-on-write overlay while shell commands are *captured,
   not executed*. You get a **predicted diff** (files + would-run commands) and
   **Commit** or **Discard** atomically — preview consequences before they're
   real. *(Cloud agents act directly on resources; there's no overlay to roll
   back.)*

MCP runs over **stdio or HTTP/SSE** transports, with **bearer-token or OAuth 2.1
(auth-code + PKCE)** auth for HTTP servers — the browser flow uses a localhost
redirect and tokens refresh automatically (Connectors view → Authorize).

10. **Counterfactual explorer** — from chat, **Explore** runs several strategies
    for the same goal as *parallel dry runs*, each in its own overlay, and shows
    a side-by-side comparison of predicted changes/cost. Commit the winner; the
    rest are discarded. Tree-search over real actions, not tokens.

Dry-run commits can be applied as a **real git commit** when the project folder
is a repo (Commit + git).

Toggle auto-routing, ambient capture, delegation, MCP, self-improvement, and the
webhook server in **Settings → Engine & safety**.

## Security, packaging, evals

- **Secrets at rest** — API keys, tokens, and MCP **OAuth/bearer credentials** are
  DPAPI-encrypted; the on-disk config and DB never hold plaintext.
- **Packaged build** — `build.bat` (PyInstaller via `aria2.spec`) produces
  `dist/ARIA2/ARIA2.exe`; schema/data load correctly when frozen.
- **Eval harness** — golden tasks scored on the engine (contains / regex /
  used_tool / created_file / no_error), organised into **per-agent suites**
  (assistant / coder / researcher / writer + `all`). Run from the **Evals** view
  or `python -m aria2.evals.run_evals [--suite coder] [--stub]`. Each run writes
  a JSON report; the Evals view **charts pass-rate over time** so regressions are
  obvious at a glance. File cases use dry runs, so evals never touch disk.
- **Auto-update channel** — set an update-manifest URL in **Settings → Updates**;
  on launch ARIA checks it and shows an update banner when a newer version is
  published, with a one-click download. (No silent self-replace; transparent and
  ready for a future signing step.)

## Ported from ARIA v1

- **Telegram bridge** — a long-poll bot lets you command ARIA from your phone.
  Only allowlisted chat IDs can issue commands (unknown senders get their ID
  echoed to add). Configure in **Settings → Messaging**.
- **PC control** — computer-use tools (screenshot, mouse, keyboard) via pyautogui,
  so the agent can drive the desktop. High-risk, so they default to "ask".
- **Tiered safety for messaging** — each Telegram session runs at a chosen access
  level: **full** (shell + file writes + full PC control), **restricted** (read /
  search / file writes; *no* shell or PC control), or **chat_only** (converse
  only). Levels map to enforced per-run tool-policy overrides. At **full**,
  shell + PC-control actions still require **host confirmation** (the desktop
  approval dialog) by default, so a remote message can't run destructive commands
  without a human at the machine approving — toggle in Settings → Messaging.
- **Calendar** — a month view to schedule one-off tasks on a day/time (they fire
  once, then disable themselves) and see what's queued.
- **Outbound notifications** — agents, scheduled tasks, and the heartbeat can
  message you on Telegram via a `notify_user` tool.
- **Discord channels** — *output* via a default webhook plus named channel
  webhooks (`post_discord(message, channel)`), and *inbound* via an optional
  gateway bot (discord.py) that runs allowlisted users' messages at the
  configured access level and replies in-channel (Settings → Messaging).
- **Proactive agents** — when notify/Discord tools are available, agents are told
  they can reach you and use them to report results or alerts unprompted.
- **Web tools** — `fetch_url`, `web_search`, `open_url` (read-only ones allowed
  by default).
- **Voice** — speak replies aloud (TTS via pyttsx3) and a 🎤 voice-input button
  in chat (optional speech recognition).
- **System tray** — minimize-to-tray keeps ARIA running in the background
  (Telegram, schedules, heartbeat) with an Open/Quit menu.
- **Heartbeat** — an optional proactive check-in on a timer that pings you only
  when something's worth your attention.
- **Sign in with OpenAI** — OAuth (auth-code + PKCE) as an alternative to an API
  key; tokens are encrypted and auto-refreshed. Configure in **Settings →
  Providers** (set the OAuth endpoints, then *Sign in with OpenAI*).

All of these are optional and degrade gracefully if their library/credentials
are absent.

- **Command palette** — press **Ctrl+K** to jump to any view or run a core action
  (new chat/project, run eval self-test, check updates) by typing.
- **Onboarding** — a first-run chat empty-state detects a missing provider key and
  routes you straight to Settings → Providers.
- **Rich chat** — assistant replies render **markdown** (bold, inline code, fenced
  code blocks, headings, bullets), each message shows a **timestamp** and a
  **Copy** button, and text is selectable.
- **File attachments** — 📎 attach files in chat (or **paste an image** with
  Ctrl+V, or **drag files onto the window**): text/code files are inlined into the
  message (capped), images are sent as vision blocks to vision-capable models
  (Claude / OpenAI / Grok / Gemini), and other files get a labelled placeholder.
- **Hover tooltips** on composer/action buttons for discoverability.
- **Projects** and **Chat** are fully separate (Claude-Code-style):
  - **Projects** is a workspace hub: a project list (each row has a ⋯ /
    right-click menu — **Rename · Edit… · Pin · Archive · Delete**; the default
    project is protected and delete cascades its chats), a live **counts** header
    (💬 chats · 📚 knowledge · ⏱ automations · 📁 folder — counts link to the
    scoped view), and **the selected project's own conversation embedded inline**
    (its chat list + transcript + composer). A project's chats open *here* — you
    are never bounced to the Chat tab. Editing name/folder/goals happens in the
    Edit dialog (no always-open form).
  - **Chat** is a standalone conversation space (project `general`), independent
    of Projects. Its chats have the same ⋯ menu (rename / pin / archive / delete)
    and search; the composer is a single compact bar that grows as you type.

## Status

Substrate + full desktop surface + the moats above + the full v1 feature set, all
verified by `python -m aria2 --smoke` (108 checks, no API key needed) and
`python -m aria2.evals.run_evals --stub`. 11 views: chat, projects, agents,
memory, knowledge, connectors, automations, calendar, runs, evals, settings.
A packaged Windows build (`build.bat` → `dist/ARIA2/ARIA2.exe`) is verified to
launch and render.

## CI / releases

`.github/workflows/release.yml` runs on each published GitHub release (or
manual dispatch): it installs deps, runs the smoke suite + eval self-test, builds
the exe with PyInstaller, zips it, generates the auto-update manifest
(`scripts/make_manifest.py` → `latest.json`, which the in-app updater polls), and
attaches the zip + manifest to the release. Point **Settings → Updates** at the
published `latest.json` URL to receive update banners.
