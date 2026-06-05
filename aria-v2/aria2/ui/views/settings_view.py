"""ui/views/settings_view.py - Providers, keys, models, safety, budgets."""

from __future__ import annotations

import customtkinter as ctk

from aria2.core import config
from aria2.ui import theme
from aria2.ui.views import widgets as w

_PROVIDERS = ["claude", "openai", "local", "grok", "gemini", "openai_compat"]
_EMBED = ["local", "voyage", "openai"]
_POLICY = ["ask", "allow", "deny"]


class SettingsView(ctk.CTkFrame):
    """Settings organised into tabs — no more one-huge-scroll."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self._entries: dict[str, ctk.CTkEntry] = {}
        s = config.load()

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(16, 4))
        ctk.CTkLabel(head, text="Settings", font=theme.f(7, "bold"),
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkLabel(head, text="Encrypted · %APPDATA%/ARIA2", font=theme.f(-2),
                     text_color=theme.TEXT_FAINT).pack(side="left", padx=16)

        tabs = ctk.CTkTabview(self, fg_color=theme.SURFACE, segmented_button_fg_color=theme.SIDEBAR,
                              segmented_button_selected_color=theme.accent(),
                              segmented_button_selected_hover_color=theme.accent(),
                              segmented_button_unselected_color=theme.SIDEBAR,
                              border_width=0)
        tabs.pack(fill="both", expand=True, padx=24, pady=(0, 24))
        for name in ("Providers", "Engine", "Messaging", "Extras", "Updates"):
            tabs.add(name)
            tabs.tab(name).grid_columnconfigure(0, weight=1)

        # Wrap each tab in a scrollable frame so long content doesn't overflow.
        def _scroll(tab):
            sf = ctk.CTkScrollableFrame(tab, fg_color="transparent")
            sf.pack(fill="both", expand=True)
            return sf

        p_tab = _scroll(tabs.tab("Providers"))
        e_tab = _scroll(tabs.tab("Engine"))
        m_tab = _scroll(tabs.tab("Messaging"))
        x_tab = _scroll(tabs.tab("Extras"))
        u_tab = _scroll(tabs.tab("Updates"))

        # ── PROVIDERS ─────────────────────────────────────────────────────────
        prov = p_tab

        # ── Providers ───────────────────────────────────────────────────────
        prov = self._section("AI Providers", prov=p_tab)
        row = ctk.CTkFrame(prov, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text="Active provider", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.provider = ctk.CTkOptionMenu(row, values=_PROVIDERS, width=140,
                                          fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.provider.set(s.get("provider", "claude"))
        self.provider.pack(side="left", padx=10)

        self._field(prov, "claude_api_key", "Claude API key", s, secret=True)
        self._field(prov, "claude_model", "Claude model", s)
        self._field(prov, "openai_api_key", "OpenAI API key", s, secret=True)
        self._field(prov, "openai_model", "OpenAI model", s)
        self._field(prov, "ollama_url", "Ollama URL", s)
        self._field(prov, "ollama_model", "Ollama model (default, loaded on startup)", s)
        self._field(prov, "ollama_idle_unload_min",
                    "Unload local model after N minutes idle (0 = never)", s)
        self._field(prov, "ollama_num_ctx",
                    "Ollama context window (tokens — match your model's num_ctx)", s)
        self._field(prov, "ollama_tool_mode",
                    "Ollama tool calling: auto / always / never", s)
        ctk.CTkButton(prov, text="🦙  Set up local AI (wizard for beginners)",
                      height=36, fg_color=theme.SURFACE_2, hover_color=theme.HOVER,
                      text_color=theme.TEXT, font=theme.f(-1),
                      command=lambda: self._open_local_wizard()).pack(
            fill="x", pady=(8, 0))

        # "Sign in with OpenAI" — real Codex PKCE flow, same as v1.
        # Uses the same public OAuth constants as the open-source Codex CLI;
        # no client-id / token-URL config needed from the user.
        from aria2.services import openai_oauth_service as _oai
        orow = ctk.CTkFrame(prov, fg_color="transparent")
        orow.pack(fill="x", pady=(10, 0))
        self._openai_signin_btn = w.ghost_button(
            orow, "Sign in with OpenAI  ↗", self._openai_signin, width=200, height=36,
            tooltip="Opens a browser tab — sign in with your ChatGPT (Plus/Pro/Team) account")
        self._openai_signin_btn.pack(side="left")
        self.openai_auth_status = ctk.CTkLabel(
            orow, text="", font=theme.f(-1), text_color=theme.TEXT_DIM)
        self.openai_auth_status.pack(side="left", padx=12)
        self._refresh_openai_status()

        # Grok (xAI).
        self._field(prov, "grok_api_key", "Grok (xAI) API key", s, secret=True)
        self._field(prov, "grok_model", "Grok model", s)
        grow = ctk.CTkFrame(prov, fg_color="transparent")
        grow.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(grow, text="Grok auth", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.grok_auth = ctk.CTkOptionMenu(grow, values=["apikey", "oauth"], width=120,
                                           fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.grok_auth.set(s.get("grok_auth_mode", "apikey"))
        self.grok_auth.pack(side="left", padx=8)
        w.ghost_button(grow, "Sign in with Grok",
                       lambda: self._provider_signin("grok", self.grok_auth_status),
                       width=160).pack(side="left")
        self.grok_auth_status = ctk.CTkLabel(grow, text="", font=theme.f(-2),
                                             text_color=theme.TEXT_DIM)
        self.grok_auth_status.pack(side="left", padx=8)
        self._field(prov, "grok_oauth_client_id", "Grok OAuth client ID", s)
        self._field(prov, "grok_oauth_auth_url", "Grok OAuth authorization URL", s)
        self._field(prov, "grok_oauth_token_url", "Grok OAuth token URL", s)
        self._field(prov, "grok_oauth_scope", "Grok OAuth scope (optional)", s)

        # Gemini (Google).
        self._field(prov, "gemini_api_key", "Gemini (Google) API key", s, secret=True)
        self._field(prov, "gemini_model", "Gemini model", s)

        # Generic OpenAI-compatible server (LM Studio / vLLM / llama.cpp / LocalAI /
        # KoboldCpp / Text-Generation-WebUI / OpenRouter). Set provider=openai_compat.
        self._field(prov, "oai_compat_base_url",
                    "OpenAI-compatible base URL (LM Studio / vLLM / OpenRouter…)", s)
        self._field(prov, "oai_compat_api_key",
                    "OpenAI-compatible API key (blank for local servers)", s, secret=True)
        self._field(prov, "oai_compat_model", "OpenAI-compatible model name", s)
        self._field(prov, "oai_compat_num_ctx",
                    "OpenAI-compatible context window (tokens)", s)
        self._field(prov, "oai_compat_tool_mode",
                    "OpenAI-compatible tool calling: auto / always / never", s)

        # ── Embeddings ──────────────────────────────────────────────────────
        emb = self._section("Embeddings (memory + knowledge)", prov=p_tab)
        row = ctk.CTkFrame(emb, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text="Provider", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.embed = ctk.CTkOptionMenu(row, values=_EMBED, width=140,
                                       fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.embed.set(s.get("embedding_provider", "local"))
        self.embed.pack(side="left", padx=10)
        self._field(emb, "voyage_api_key", "Voyage API key", s, secret=True)

        # ── Engine & safety ─────────────────────────────────────────────────
        eng = self._section("Engine & safety", prov=e_tab)
        row = ctk.CTkFrame(eng, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text="Default tool policy", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.policy = ctk.CTkOptionMenu(row, values=_POLICY, width=120,
                                        fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.policy.set(s.get("default_tool_policy", "ask"))
        self.policy.pack(side="left", padx=10)

        self.caching = ctk.CTkCheckBox(eng, text="Prompt caching (cheaper multi-turn)",
                                       font=theme.f(-1))
        self.caching.pack(anchor="w", pady=6)
        if s.get("prompt_caching", True):
            self.caching.select()

        self.auto_route = ctk.CTkCheckBox(
            eng, text="Auto-route models (cheap model for trivial tasks, strong for hard)",
            font=theme.f(-1))
        self.auto_route.pack(anchor="w", pady=6)
        if s.get("auto_route", False):
            self.auto_route.select()

        self.ambient = ctk.CTkCheckBox(
            eng, text="Ambient capture (watch project folders, suggest automations) — local only",
            font=theme.f(-1))
        self.ambient.pack(anchor="w", pady=6)
        if s.get("ambient_enabled", False):
            self.ambient.select()

        self.delegation = ctk.CTkCheckBox(
            eng, text="Delegation (agents can spawn parallel worker sub-agents; routing learns)",
            font=theme.f(-1))
        self.delegation.pack(anchor="w", pady=6)
        if s.get("delegation_enabled", True):
            self.delegation.select()

        self.mcp = ctk.CTkCheckBox(
            eng, text="MCP connectors (expose external tool servers to agents)",
            font=theme.f(-1))
        self.mcp.pack(anchor="w", pady=6)
        if s.get("mcp_enabled", True):
            self.mcp.select()

        self.self_improve = ctk.CTkCheckBox(
            eng, text="Self-improvement (on failed runs, propose agent fixes for review)",
            font=theme.f(-1))
        self.self_improve.pack(anchor="w", pady=6)
        if s.get("self_improvement_enabled", False):
            self.self_improve.select()

        self.webhook = ctk.CTkCheckBox(
            eng, text="Webhook server (localhost listener that fires webhook triggers)",
            font=theme.f(-1))
        self.webhook.pack(anchor="w", pady=6)
        if s.get("webhook_enabled", False):
            self.webhook.select()

        self._field(eng, "max_tokens", "Max output tokens", s)
        self._field(eng, "max_iterations", "Max agent iterations", s)
        self._field(eng, "default_run_budget_usd", "Default run budget (USD)", s)
        self._field(eng, "accent", "Accent colour (hex)", s)

        # Font size — live slider (rebuilds views on release so all widgets
        # immediately reflect the new size without a restart).
        fs_row = ctk.CTkFrame(eng, fg_color="transparent")
        fs_row.pack(fill="x", pady=8)
        ctk.CTkLabel(fs_row, text="Font size", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self._font_label = ctk.CTkLabel(fs_row, text=str(s.get("font_size", 13)),
                                        font=theme.f(-1), text_color=theme.TEXT, width=24)
        self._font_label.pack(side="right", padx=4)
        self._font_slider = ctk.CTkSlider(
            eng, from_=10, to=20, number_of_steps=10,
            command=self._on_font_slide)
        self._font_slider.set(int(s.get("font_size", 13)))
        self._font_slider.pack(fill="x", pady=(0, 4))
        self._font_slider.bind("<ButtonRelease-1>", self._on_font_release)
        self._font_slider.bind("<ButtonRelease-2>", self._on_font_release)

        self.computer = ctk.CTkCheckBox(
            eng, text="Computer use (allow mouse/keyboard/screen tools) — high risk",
            font=theme.f(-1))
        self.computer.pack(anchor="w", pady=6)
        if s.get("computer_use_enabled", False):
            self.computer.select()

        # ── Messaging (Telegram) ────────────────────────────────────────────
        msg = self._section("Messaging (Telegram)", prov=m_tab)
        tg_head = ctk.CTkFrame(msg, fg_color="transparent")
        tg_head.pack(fill="x")
        self.messaging = ctk.CTkCheckBox(tg_head, text="Enable Telegram bridge",
                                         font=theme.f(-1))
        self.messaging.pack(side="left", pady=6)
        ctk.CTkButton(tg_head, text="ℹ  How to set up Telegram", width=200, height=28,
                      fg_color=theme.SURFACE_2, hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM, font=theme.f(-1),
                      command=lambda: self._show_guide("telegram")).pack(
            side="right", padx=(8, 0), pady=6)
        self.messaging.pack(anchor="w", pady=6)
        if s.get("messaging_enabled", False):
            self.messaging.select()
        self._field(msg, "telegram_bot_token", "Bot token", s, secret=True)
        self._field(msg, "telegram_allowlist", "Allowed chat IDs (comma-separated)", s)
        arow = ctk.CTkFrame(msg, fg_color="transparent")
        arow.pack(fill="x", pady=6)
        ctk.CTkLabel(arow, text="Access level", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.msg_access = ctk.CTkOptionMenu(
            arow, values=["chat_only", "restricted", "full"], width=140,
            fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.msg_access.set(s.get("messaging_access", "restricted"))
        self.msg_access.pack(side="left", padx=8)

        prow = ctk.CTkFrame(msg, fg_color="transparent")
        prow.pack(fill="x", pady=6)
        ctk.CTkLabel(prow, text="AI model for messages", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.msg_provider = ctk.CTkOptionMenu(
            prow, values=["default", "local", "cloud"], width=140,
            fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.msg_provider.set(s.get("messaging_provider", "default"))
        self.msg_provider.pack(side="left", padx=8)
        ctk.CTkLabel(prow, text="default=Settings provider  local=Ollama  cloud=Claude/GPT/Grok",
                     font=theme.f(-2), text_color=theme.TEXT_FAINT).pack(side="left", padx=6)

        test_row = ctk.CTkFrame(msg, fg_color="transparent")
        test_row.pack(fill="x", pady=4)
        w.ghost_button(test_row, "🔔  Send test notification", self._test_telegram,
                       width=200, height=30, tooltip="Send a test message to verify Telegram is working").pack(side="left")
        self.tg_test_lbl = ctk.CTkLabel(test_row, text="", font=theme.f(-1),
                                        text_color=theme.TEXT_DIM)
        self.tg_test_lbl.pack(side="left", padx=10)
        ctk.CTkLabel(
            msg, text="full = shell + PC control · restricted = files/search only, "
                      "no PC control · chat_only = converse only",
            font=theme.f(-2), text_color=theme.TEXT_FAINT, wraplength=560,
            justify="left").pack(anchor="w", pady=(0, 4))
        self.msg_confirm = ctk.CTkCheckBox(
            msg, text="Require host confirmation for full-access shell/PC actions "
                      "(recommended)", font=theme.f(-1))
        self.msg_confirm.pack(anchor="w", pady=(2, 4))
        if s.get("messaging_require_confirmation", True):
            self.msg_confirm.select()

        # Discord output channels.
        dc_head = ctk.CTkFrame(msg, fg_color="transparent")
        dc_head.pack(fill="x", pady=(8, 2))
        ctk.CTkLabel(dc_head, text="Discord", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkButton(dc_head, text="ℹ  How to set up Discord", width=200, height=28,
                      fg_color=theme.SURFACE_2, hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM, font=theme.f(-1),
                      command=lambda: self._show_guide("discord")).pack(
            side="right", padx=(8, 0))
        self._field(msg, "discord_webhook_url", "Discord default webhook URL", s, secret=True)
        ctk.CTkLabel(msg, text="Named Discord channels (one per line, name=webhook_url)",
                     font=theme.f(-1), text_color=theme.TEXT_DIM).pack(anchor="w", pady=(6, 2))
        self.discord_channels = ctk.CTkTextbox(msg, height=80, fg_color=theme.SURFACE_2,
                                               font=theme.f(-1), wrap="none")
        self.discord_channels.pack(fill="x")
        existing = "\n".join(f"{c.get('name','')}={c.get('url','')}"
                             for c in s.get("discord_channels", []))
        if existing:
            self.discord_channels.insert("1.0", existing)

        # Discord inbound (gateway bot).
        self.discord_inbound = ctk.CTkCheckBox(
            msg, text="Discord inbound bot (needs discord.py)", font=theme.f(-1))
        self.discord_inbound.pack(anchor="w", pady=(8, 4))
        if s.get("discord_inbound_enabled", False):
            self.discord_inbound.select()
        self._field(msg, "discord_bot_token", "Discord bot token", s, secret=True)
        self._field(msg, "discord_allowlist", "Allowed Discord user IDs (comma-separated)", s)

        # ── Extras (browser / voice / tray) ─────────────────────────────────
        ext = self._section("Extras", prov=x_tab)
        self.browser = ctk.CTkCheckBox(ext, text="Web tools (fetch / search / open URL)",
                                       font=theme.f(-1))
        self.browser.pack(anchor="w", pady=4)
        if s.get("browser_enabled", True):
            self.browser.select()
        self.tts = ctk.CTkCheckBox(ext, text="Speak replies aloud (TTS)", font=theme.f(-1))
        self.tts.pack(anchor="w", pady=4)
        if s.get("tts_enabled", False):
            self.tts.select()
        self.tray = ctk.CTkCheckBox(ext, text="System tray icon (minimize to tray, keep running)",
                                    font=theme.f(-1))
        self.tray.pack(anchor="w", pady=4)
        if s.get("tray_enabled", False):
            self.tray.select()

        from aria2.services import startup_service as _su
        self.autostart = ctk.CTkCheckBox(
            ext, text="Launch ARIA automatically when Windows starts",
            font=theme.f(-1))
        self.autostart.pack(anchor="w", pady=4)
        if _su.is_enabled():
            self.autostart.select()

        # ── Heartbeat (proactive check-in) ──────────────────────────────────
        hb = self._section("Heartbeat (proactive check-in)", prov=x_tab)
        self.heartbeat = ctk.CTkCheckBox(hb, text="Enable periodic check-in", font=theme.f(-1))
        self.heartbeat.pack(anchor="w", pady=4)
        if s.get("heartbeat_enabled", False):
            self.heartbeat.select()
        self._field(hb, "heartbeat_interval", "Interval (minutes)", s)
        self._field(hb, "heartbeat_prompt", "Prompt (blank = default check-in)", s)

        # ── Updates ─────────────────────────────────────────────────────────
        upd = self._section("Updates", prov=u_tab)
        import aria2 as _aria2
        ctk.CTkLabel(upd, text=f"Current version:  v{_aria2.__version__}",
                     font=theme.f(0, "bold"), text_color=theme.TEXT).pack(
            anchor="w", pady=(0, 6))
        self._field(upd, "update_manifest_url", "Update manifest URL", s)
        # Heal a blank manifest URL: show the built-in default so a Save persists
        # it (older configs saved an empty value, which broke update checks).
        _murl = self._entries["update_manifest_url"]
        if not _murl.get().strip():
            _murl.insert(0, config.DEFAULTS.get("update_manifest_url", ""))
        self.auto_upd = ctk.CTkCheckBox(upd, text="Check for updates on launch",
                                        font=theme.f(-1))
        self.auto_upd.pack(anchor="w", pady=6)
        if s.get("auto_check_updates", True):
            self.auto_upd.select()
        urow = ctk.CTkFrame(upd, fg_color="transparent")
        urow.pack(fill="x", pady=6)
        w.ghost_button(urow, "Check now", self._check_update, width=110).pack(side="left")
        self.dl_btn = w.primary_button(urow, "Download update", self._download_update, width=150)
        self.update_status = ctk.CTkLabel(urow, text="", font=theme.f(-1),
                                          text_color=theme.TEXT_DIM)
        self.update_status.pack(side="left", padx=10)
        self._pending_update = None

        # ── Sticky save bar ──────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color=theme.SIDEBAR,
                           border_width=1, border_color=theme.BORDER)
        bar.pack(fill="x", side="bottom")
        w.primary_button(bar, "Save settings", self._save, width=160, height=38).pack(
            side="left", padx=16, pady=8)
        self.status = ctk.CTkLabel(bar, text="", font=theme.f(-1),
                                   text_color=theme.TEXT_FAINT)
        self.status.pack(side="left", padx=8)

        self._dirty = False
        self._bind_dirty_tracking(self)

    def _on_font_slide(self, value):
        """Called on every slider move — update the label, mark dirty."""
        size = int(value)
        self._font_label.configure(text=str(size))
        self.mark_dirty()

    def _on_font_release(self, _event=None):
        """Apply font size live when the user releases the slider."""
        size = int(self._font_slider.get())
        from aria2.core import config as _cfg
        s = _cfg.load()
        if int(s.get("font_size", 13)) == size:
            return  # no change
        s["font_size"] = size
        _cfg.save(s)
        self._dirty = False  # we saved, so no pending-change nag
        if hasattr(self, "app"):
            self.app.toast(f"Font size set to {size} — rebuilding…", "info", 2500)
            self.after(100, self.app.rebuild_views)

    def _bind_dirty_tracking(self, parent):
        """Recursively bind change events on all input widgets to mark_dirty."""
        import customtkinter as _ctk
        for child in parent.winfo_children():
            cls = type(child).__name__
            if cls == "CTkEntry":
                child.bind("<KeyRelease>", self.mark_dirty, add="+")
            elif cls == "CTkCheckBox":
                child.configure(command=self.mark_dirty)
            elif cls == "CTkOptionMenu":
                child.configure(command=self.mark_dirty)
            elif cls == "CTkTextbox":
                child.bind("<KeyRelease>", self.mark_dirty, add="+")
            try:
                self._bind_dirty_tracking(child)
            except Exception:
                pass

    def on_show(self):
        self._dirty = False
        self._refresh_openai_status()

    def mark_dirty(self, *_):
        """Called by any widget that changes a setting."""
        self._dirty = True

    def confirm_leave(self) -> bool:
        """Return True if it's safe to navigate away. Shows a warning if dirty."""
        if not getattr(self, "_dirty", False):
            return True
        from tkinter import messagebox
        answer = messagebox.askyesnocancel(
            "Unsaved settings",
            "You have unsaved settings.\n\nSave before leaving?",
            parent=self)
        if answer is None:    # Cancel — stay on Settings
            return False
        if answer:            # Yes — save then leave
            self._save()
        return True           # No — discard and leave

    def _open_local_wizard(self):
        from aria2.ui.views.local_ai_wizard import open_wizard
        open_wizard(self)

    def _test_telegram(self):
        import threading
        from aria2.services import messaging_service
        self.tg_test_lbl.configure(text="Sending…", text_color=theme.TEXT_DIM)

        def worker():
            res = messaging_service.notify(
                "✅ ARIA test notification — Telegram is connected and working!")
            sent = res.get("sent", 0)
            err = res.get("error", "")
            if sent:
                msg, color = f"✓ Sent to {sent} chat(s)", theme.SUCCESS
            else:
                msg = f"✗ Failed: {err or 'no chat IDs in allowlist'}"
                color = theme.DANGER
            self.after(0, lambda: self.tg_test_lbl.configure(text=msg, text_color=color))

        threading.Thread(target=worker, daemon=True).start()

    def _show_guide(self, which: str):
        from aria2.ui.views.setup_guides import show_telegram_guide, show_discord_guide
        if which == "telegram":
            show_telegram_guide(self)
        else:
            show_discord_guide(self)

    def _refresh_openai_status(self):
        from aria2.services import openai_oauth_service as _oai
        if _oai.is_signed_in():
            name = _oai.get_display_name()
            self.openai_auth_status.configure(
                text=f"✓ Signed in  ({name})" if name else "✓ Signed in",
                text_color=theme.SUCCESS)
            self._openai_signin_btn.configure(text="Sign out of OpenAI",
                                              command=self._openai_signout)
        else:
            self.openai_auth_status.configure(text="Not signed in", text_color=theme.TEXT_FAINT)
            self._openai_signin_btn.configure(text="Sign in with OpenAI  ↗",
                                              command=self._openai_signin)

    def _openai_signin(self):
        from aria2.services import openai_oauth_service as _oai
        self.openai_auth_status.configure(
            text="Opening browser… (waiting up to 5 min)", text_color=theme.TEXT_DIM)
        self._openai_signin_btn.configure(state="disabled")

        def _ok(tokens):
            self.after(0, lambda: (
                self.openai_auth_status.configure(
                    text=f"✓ Signed in  ({_oai.get_display_name()})",
                    text_color=theme.SUCCESS),
                self._openai_signin_btn.configure(
                    state="normal", text="Sign out of OpenAI",
                    command=self._openai_signout)))

        def _err(msg):
            self.after(0, lambda: (
                self.openai_auth_status.configure(
                    text=f"✗ {msg[:60]}", text_color=theme.DANGER),
                self._openai_signin_btn.configure(state="normal")))

        _oai.start_login(_ok, _err)

    def _openai_signout(self):
        from aria2.services import openai_oauth_service as _oai
        _oai.sign_out()
        self._refresh_openai_status()

    def _provider_signin(self, prefix: str, status_label):
        import threading
        from aria2.services import provider_auth
        # Persist the OAuth config first so authorize() can read it.
        self._save(silent=True)
        status_label.configure(text="Opening browser…", text_color=theme.TEXT_DIM)

        def worker():
            res = provider_auth.authorize(None, prefix)
            msg = "signed in ✓" if res.get("ok") else f"✗ {res.get('error','failed')[:40]}"
            color = theme.SUCCESS if res.get("ok") else theme.DANGER
            self.after(0, lambda: status_label.configure(text=msg, text_color=color))

        threading.Thread(target=worker, daemon=True).start()

    def _check_update(self):
        import threading
        from aria2.services import update_service
        self.update_status.configure(text="Checking…", text_color=theme.TEXT_DIM)
        url = self._entries["update_manifest_url"].get().strip()

        def worker():
            st = update_service.check_status(url or None)
            self.after(0, lambda: self._show_update(st))

        threading.Thread(target=worker, daemon=True).start()

    def _show_update(self, st):
        # st is the rich check_status() result: distinguishes update / current /
        # error and always carries the running version, so a failed check no
        # longer masquerades as "up to date".
        status = (st or {}).get("status", "error")
        cur = (st or {}).get("current", "?")
        self._pending_update = st if status == "update" else None
        if status == "update":
            self.update_status.configure(
                text=f"Update available: v{st['version']}  (you have v{cur})"
                     + (f" — {st.get('notes','')[:40]}" if st.get('notes') else ""),
                text_color=theme.WARN)
            from aria2.services import update_service as _us
            self.dl_btn.configure(
                text="⬇  Update & restart" if _us.is_frozen() else "⬇  Download zip",
                state="normal")
            self.dl_btn.pack(side="left", padx=6)
        elif status == "error":
            self.update_status.configure(
                text=f"Couldn't check for updates (on v{cur}): {st.get('error','')[:60]}",
                text_color=theme.DANGER)
            self.dl_btn.pack_forget()
        else:  # current
            self.update_status.configure(text=f"Up to date ✓  (v{cur})",
                                         text_color=theme.SUCCESS)
            self.dl_btn.pack_forget()

    def _download_update(self):
        import threading
        from aria2.services import update_service
        if not self._pending_update:
            return
        url = self._pending_update.get("url", "")

        if update_service.is_frozen():
            # Packaged build: download + install in place, then restart.
            self.dl_btn.configure(state="disabled")
            self.update_status.configure(text="Starting update…", text_color=theme.TEXT_DIM)

            def worker():
                res = update_service.download_and_install(
                    url, on_status=lambda m: self.after(
                        0, lambda: self.update_status.configure(
                            text=m, text_color=theme.TEXT_DIM)))
                if res.get("ok") and res.get("relaunch"):
                    self.after(0, self._restart_for_update)
                else:
                    self.after(0, lambda: (
                        self.update_status.configure(
                            text=f"✗ {res.get('error', 'update failed')[:70]}",
                            text_color=theme.DANGER),
                        self.dl_btn.configure(state="normal")))

            threading.Thread(target=worker, daemon=True).start()
            return

        # Running from source: just download the zip (manual install).
        self.update_status.configure(text="Downloading…", text_color=theme.TEXT_DIM)

        def worker():
            res = update_service.download_update(url)
            msg = (f"Downloaded → {res['path']}" if res.get("ok")
                   else f"✗ {res.get('error', 'failed')[:50]}")
            self.after(0, lambda: self.update_status.configure(
                text=msg, text_color=theme.SUCCESS if res.get("ok") else theme.DANGER))

        threading.Thread(target=worker, daemon=True).start()

    def _restart_for_update(self):
        """Quit so the detached updater can replace the files and relaunch."""
        self.update_status.configure(text="Update staged — restarting ARIA…",
                                     text_color=theme.SUCCESS)
        if hasattr(self, "app"):
            self.after(1200, self.app._real_quit)

    def _section(self, title: str, prov=None) -> ctk.CTkFrame:
        parent = prov if prov is not None else self
        card = w.card(parent)
        card.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(card, text=title, font=theme.f(1, "bold"), text_color=theme.TEXT).pack(
            anchor="w", padx=14, pady=(10, 4))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=(0, 12))
        return inner

    def _field(self, parent, key: str, label: str, s: dict, secret: bool = False):
        raw = s.get(key, "")
        val = ",".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
        frame, entry = w.labeled_entry(parent, label, val, show="•" if secret else None)
        frame.pack(fill="x", pady=4)
        self._entries[key] = entry

    def _save(self, silent: bool = False):
        s = config.load()
        s["provider"] = self.provider.get()
        s["embedding_provider"] = self.embed.get()
        s["default_tool_policy"] = self.policy.get()
        s["prompt_caching"] = bool(self.caching.get())
        s["auto_route"] = bool(self.auto_route.get())
        s["delegation_enabled"] = bool(self.delegation.get())
        s["mcp_enabled"] = bool(self.mcp.get())
        s["self_improvement_enabled"] = bool(self.self_improve.get())
        webhook_on = bool(self.webhook.get())
        s["webhook_enabled"] = webhook_on
        s["auto_check_updates"] = bool(self.auto_upd.get())
        s["computer_use_enabled"] = bool(self.computer.get())
        # openai_auth_mode is set by the sign-in/sign-out buttons, not saved here.
        s["grok_auth_mode"] = self.grok_auth.get()
        s["browser_enabled"] = bool(self.browser.get())
        s["tts_enabled"] = bool(self.tts.get())
        tray_on = bool(self.tray.get())
        s["tray_enabled"] = tray_on
        autostart_on = bool(self.autostart.get())
        from aria2.services import startup_service as _su
        _su.set_enabled(autostart_on)
        heartbeat_on = bool(self.heartbeat.get())
        s["heartbeat_enabled"] = heartbeat_on
        messaging_on = bool(self.messaging.get())
        s["messaging_enabled"] = messaging_on
        s["messaging_access"] = self.msg_access.get()
        s["messaging_provider"] = self.msg_provider.get()
        s["messaging_require_confirmation"] = bool(self.msg_confirm.get())
        # Parse named Discord channels (name=url per line).
        channels = []
        for line in self.discord_channels.get("1.0", "end").splitlines():
            line = line.strip()
            if "=" in line:
                name, url = line.split("=", 1)
                if name.strip() and url.strip():
                    channels.append({"name": name.strip(), "url": url.strip()})
        s["discord_channels"] = channels
        discord_in_on = bool(self.discord_inbound.get())
        s["discord_inbound_enabled"] = discord_in_on
        ambient_on = bool(self.ambient.get())
        s["ambient_enabled"] = ambient_on
        for key, entry in self._entries.items():
            v = entry.get().strip()
            if key in ("max_tokens", "max_iterations", "heartbeat_interval",
                       "ollama_num_ctx", "oai_compat_num_ctx"):
                try:
                    v = int(v)
                except ValueError:
                    continue
            elif key == "default_run_budget_usd":
                try:
                    v = float(v)
                except ValueError:
                    continue
            elif key in ("telegram_allowlist", "discord_allowlist"):
                v = [x.strip() for x in v.split(",") if x.strip()]
            s[key] = v
        config.save(s)
        self._dirty = False
        if silent:
            return
        # Apply heartbeat + tray toggles live.
        from aria2.services import heartbeat_service, tray_service
        if heartbeat_on:
            heartbeat_service.heartbeat.start()
        else:
            heartbeat_service.heartbeat.stop()
        if tray_on:
            tray_service.tray.start(self.app)
        else:
            tray_service.tray.stop()
        # Apply the Telegram bridge toggle live.
        from aria2.services import messaging_service
        if messaging_on:
            messaging_service.bridge.start()
        else:
            messaging_service.bridge.stop()
        if discord_in_on:
            messaging_service.discord_bridge.start()
        else:
            messaging_service.discord_bridge.stop()
        # Apply toggles live.
        from aria2.services import ambient_service, automation_service
        if ambient_on:
            ambient_service.watcher.start()
        else:
            ambient_service.watcher.stop()
        if webhook_on:
            automation_service.webhook_server.start()
        else:
            automation_service.webhook_server.stop()
        # Restart model manager if provider changed.
        try:
            from aria2.services import ollama_model_manager as _omm
            _omm.model_manager.stop(); _omm.model_manager.start()
        except Exception:
            pass
        self.status.configure(text="")
        if hasattr(self, "app"):
            self.app.toast("Settings saved", "success")
