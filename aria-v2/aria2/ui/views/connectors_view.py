"""ui/views/connectors_view.py - Manage MCP connectors (external tool servers).

Add a connector by command (stdio), test it to discover its tools, enable/disable,
and delete. Discovered tools flow into every run through the same permission gate
as built-in tools, so connecting an ecosystem of MCP servers is the fastest way
to expand what ARIA can do — without writing bespoke plugins.
"""

from __future__ import annotations

import json
import threading

import customtkinter as ctk

from aria2.services import connector_service
from aria2.ui import theme
from aria2.ui.views import widgets as w


class ConnectorsView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.selected: str | None = None
        w.header(self, "Connectors", "Connect MCP servers; their tools join ARIA's "
                                     "permission-gated tool registry.")

        from aria2.ui.views.paned_view import make_paned
        left, right = make_paned(self, "sidebar_connectors_width",
                                 default_w=240, min_w=160, max_w=460)
        w.primary_button(left, "+  New connector", self._new, height=34).pack(
            fill="x", padx=10, pady=10)
        self.list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list.pack(fill="both", expand=True)

        self.form = w.card(right)
        self.form.pack(fill="both", expand=True)
        self._build_form()

    def on_show(self):
        self._refresh_list()

    def _refresh_list(self):
        for c in self.list.winfo_children():
            c.destroy()
        conns = connector_service.list_connectors()
        if not conns:
            ctk.CTkLabel(self.list, text="No connectors yet.", font=theme.f(-1),
                         text_color=theme.TEXT_FAINT).pack(anchor="w", padx=8, pady=8)
        for c in conns:
            active = c["id"] == self.selected
            dot = "🟢" if c["enabled"] else "⚪"
            ctk.CTkButton(
                self.list, text=f"{dot}  {c['name']}", anchor="w", height=34,
                fg_color=theme.SURFACE_2 if active else "transparent",
                hover_color=theme.SURFACE_2, text_color=theme.TEXT if active else theme.TEXT_DIM,
                font=theme.f(-1), command=lambda i=c["id"]: self._select(i),
            ).pack(fill="x", padx=6, pady=1)

    def _build_form(self):
        pad = {"padx": 18, "pady": (6, 0)}
        self.name_f, self.name_e = w.labeled_entry(self.form, "Name")
        self.name_f.pack(fill="x", **pad)

        trow = ctk.CTkFrame(self.form, fg_color="transparent")
        trow.pack(fill="x", padx=18, pady=(8, 0))
        ctk.CTkLabel(trow, text="Transport", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.transport = ctk.CTkOptionMenu(trow, values=["stdio", "http"], width=110,
                                           command=lambda *_: self._on_transport(),
                                           fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.transport.pack(side="left", padx=8)

        # stdio fields
        self.stdio_box = ctk.CTkFrame(self.form, fg_color="transparent")
        self.cmd_f, self.cmd_e = w.labeled_entry(self.stdio_box, "Command (e.g. python, npx, node)")
        self.cmd_f.pack(fill="x", pady=(6, 0))
        self.args_f, self.args_e = w.labeled_entry(self.stdio_box, "Arguments (space-separated)")
        self.args_f.pack(fill="x", pady=(6, 0))

        # http fields
        self.http_box = ctk.CTkFrame(self.form, fg_color="transparent")
        self.url_f, self.url_e = w.labeled_entry(self.http_box, "Server URL (https://…/mcp)")
        self.url_f.pack(fill="x", pady=(6, 0))

        self.env_f, self.env_e = w.labeled_entry(
            self.form, "Env / headers (KEY=VALUE,KEY2=VALUE2)")
        self.env_f.pack(fill="x", **pad)

        # ── Auth (HTTP connectors) ──────────────────────────────────────────
        arow = ctk.CTkFrame(self.form, fg_color="transparent")
        arow.pack(fill="x", padx=18, pady=(8, 0))
        ctk.CTkLabel(arow, text="Auth", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.auth_type = ctk.CTkOptionMenu(arow, values=["none", "bearer", "oauth"],
                                           width=110, command=lambda *_: self._on_auth(),
                                           fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.auth_type.pack(side="left", padx=8)
        self.authorize_btn = w.ghost_button(arow, "Authorize", self._authorize, width=100)
        self.authorize_btn.pack(side="left")
        self.auth_status = ctk.CTkLabel(arow, text="", font=theme.f(-2),
                                        text_color=theme.TEXT_DIM)
        self.auth_status.pack(side="left", padx=8)

        self.bearer_box = ctk.CTkFrame(self.form, fg_color="transparent")
        self.token_f, self.token_e = w.labeled_entry(self.bearer_box, "Bearer token", show="•")
        self.token_f.pack(fill="x")

        self.oauth_box = ctk.CTkFrame(self.form, fg_color="transparent")
        self.cid_f, self.cid_e = w.labeled_entry(self.oauth_box, "Client ID")
        self.cid_f.pack(fill="x", pady=(4, 0))
        self.csec_f, self.csec_e = w.labeled_entry(self.oauth_box, "Client secret (optional)", show="•")
        self.csec_f.pack(fill="x", pady=(4, 0))
        self.aurl_f, self.aurl_e = w.labeled_entry(self.oauth_box, "Authorization URL (blank = discover)")
        self.aurl_f.pack(fill="x", pady=(4, 0))
        self.turl_f, self.turl_e = w.labeled_entry(self.oauth_box, "Token URL (blank = discover)")
        self.turl_f.pack(fill="x", pady=(4, 0))
        self.scope_f, self.scope_e = w.labeled_entry(self.oauth_box, "Scope (optional)")
        self.scope_f.pack(fill="x", pady=(4, 0))

        self.enabled = ctk.CTkCheckBox(self.form, text="Enabled", font=theme.f(-1))
        self.enabled.pack(anchor="w", padx=18, pady=10)
        self.enabled.select()

        btns = ctk.CTkFrame(self.form, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=8)
        w.primary_button(btns, "Save", self._save, width=90).pack(side="left")
        w.ghost_button(btns, "Test", self._test, width=80).pack(side="left", padx=6)
        w.ghost_button(btns, "Delete", self._delete, width=80).pack(side="left")
        self.status = ctk.CTkLabel(btns, text="", font=theme.f(-1), text_color=theme.TEXT_DIM)
        self.status.pack(side="left", padx=8)

        ctk.CTkLabel(self.form, text="Discovered tools", font=theme.f(-1, "bold"),
                     text_color=theme.accent()).pack(anchor="w", padx=18, pady=(8, 2))
        self.tools = ctk.CTkScrollableFrame(self.form, fg_color="transparent", height=180)
        self.tools.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        hint = ("Tip: try transport stdio, command  python , args  -m aria2.devtools."
                "echo_mcp_server  to connect the built-in echo test server.")
        ctk.CTkLabel(self.form, text=hint, font=theme.f(-2), text_color=theme.TEXT_FAINT,
                     wraplength=520, justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        self._on_transport()
        self._on_auth()

    def _on_transport(self):
        self.stdio_box.pack_forget()
        self.http_box.pack_forget()
        if self.transport.get() == "http":
            self.http_box.pack(fill="x", padx=18, before=self.env_f)
        else:
            self.stdio_box.pack(fill="x", padx=18, before=self.env_f)

    def _on_auth(self):
        self.bearer_box.pack_forget()
        self.oauth_box.pack_forget()
        at = self.auth_type.get()
        if at == "bearer":
            self.bearer_box.pack(fill="x", padx=18, before=self.enabled)
        elif at == "oauth":
            self.oauth_box.pack(fill="x", padx=18, before=self.enabled)
        self.authorize_btn.configure(state="normal" if at == "oauth" else "disabled")

    def _authorize(self):
        if not self.selected:
            self._save()
        if not self.selected:
            return
        self.auth_status.configure(text="Opening browser…", text_color=theme.TEXT_DIM)

        def worker():
            res = connector_service.begin_oauth(self.selected)
            msg = ("authorized ✓" if res.get("ok")
                   else f"✗ {res.get('error', 'failed')[:40]}")
            color = theme.SUCCESS if res.get("ok") else theme.DANGER
            self.after(0, lambda: self.auth_status.configure(text=msg, text_color=color))

        threading.Thread(target=worker, daemon=True).start()

    def _new(self):
        self.selected = None
        self._loaded_auth = {}
        for e in (self.name_e, self.cmd_e, self.args_e, self.env_e, self.url_e,
                  self.token_e, self.cid_e, self.csec_e, self.aurl_e, self.turl_e,
                  self.scope_e):
            e.delete(0, "end")
        self.transport.set("stdio")
        self.auth_type.set("none")
        self.auth_status.configure(text="")
        self._on_transport()
        self._on_auth()
        self.enabled.select()
        self.status.configure(text="New connector")
        for c in self.tools.winfo_children():
            c.destroy()

    def _select(self, cid: str):
        self.selected = cid
        c = connector_service.get(cid)
        self.name_e.delete(0, "end"); self.name_e.insert(0, c["name"])
        self.transport.set(c["transport"] or "stdio")
        self.cmd_e.delete(0, "end"); self.cmd_e.insert(0, c["command"] or "")
        self.args_e.delete(0, "end")
        self.args_e.insert(0, " ".join(json.loads(c["args_json"] or "[]")))
        self.url_e.delete(0, "end"); self.url_e.insert(0, c["url"] or "")
        self.env_e.delete(0, "end")
        env = json.loads(c["env_json"] or "{}")
        self.env_e.insert(0, ",".join(f"{k}={v}" for k, v in env.items()))
        # Auth (decrypted accessor — UI never sees ciphertext)
        self._loaded_auth = connector_service.read_auth(cid)
        at = self._loaded_auth.get("type", "none")
        self.auth_type.set(at)
        for e, key in ((self.token_e, "token"), (self.cid_e, "client_id"),
                       (self.csec_e, "client_secret"), (self.aurl_e, "authorization_url"),
                       (self.turl_e, "token_url"), (self.scope_e, "scope")):
            e.delete(0, "end"); e.insert(0, self._loaded_auth.get(key, "") or "")
        has_tok = bool(self._loaded_auth.get("access_token"))
        self.auth_status.configure(text="authorized ✓" if has_tok else "",
                                   text_color=theme.SUCCESS)
        (self.enabled.select if c["enabled"] else self.enabled.deselect)()
        self._on_transport()
        self._on_auth()
        self.status.configure(text="")
        for x in self.tools.winfo_children():
            x.destroy()
        self._refresh_list()

    def _collect(self) -> dict:
        args = [a for a in self.args_e.get().strip().split(" ") if a]
        env = {}
        for pair in self.env_e.get().strip().split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                env[k.strip()] = v.strip()
        at = self.auth_type.get()
        # Preserve already-obtained OAuth tokens when editing config.
        auth = dict(getattr(self, "_loaded_auth", {}) or {})
        auth["type"] = at
        if at == "bearer":
            auth = {"type": "bearer", "token": self.token_e.get().strip()}
        elif at == "oauth":
            auth.update({
                "client_id": self.cid_e.get().strip(),
                "client_secret": self.csec_e.get().strip(),
                "authorization_url": self.aurl_e.get().strip(),
                "token_url": self.turl_e.get().strip(),
                "scope": self.scope_e.get().strip(),
            })
        elif at == "none":
            auth = {"type": "none"}
        return {
            "name": self.name_e.get().strip() or "Connector",
            "transport": self.transport.get(),
            "command": self.cmd_e.get().strip(),
            "args": args, "env": env, "url": self.url_e.get().strip(),
            "auth": auth, "enabled": bool(self.enabled.get()),
        }

    def _save(self):
        data = self._collect()
        if self.selected:
            connector_service.update(self.selected, data)
        else:
            created = connector_service.create(
                data["name"], data["command"], args=data["args"], env=data["env"],
                transport=data["transport"], url=data["url"], auth=data["auth"],
                enabled=data["enabled"])
            self.selected = created["id"]
        self._loaded_auth = data["auth"]
        self.status.configure(text="")
        self.app.toast("Connector saved", "success")
        self._refresh_list()

    def _delete(self):
        if self.selected:
            connector_service.delete(self.selected)
            self._new()
            self._refresh_list()

    def _test(self):
        if not self.selected:
            self._save()
        if not self.selected:
            return
        self.status.configure(text="Connecting…", text_color=theme.TEXT_DIM)
        for x in self.tools.winfo_children():
            x.destroy()

        def worker():
            res = connector_service.test_connection(self.selected)
            self.after(0, lambda: self._show_test(res))

        threading.Thread(target=worker, daemon=True).start()

    def _show_test(self, res: dict):
        if res.get("error"):
            self.status.configure(text="")
            self.app.toast(f"Connection failed: {res['error'][:60]}", "error")
            return
        tools = res.get("tools", [])
        self.status.configure(text="")
        self.app.toast(f"Connected — {len(tools)} tools discovered", "success")
        for t in tools:
            card = ctk.CTkFrame(self.tools, fg_color=theme.SURFACE_2, corner_radius=6)
            card.pack(fill="x", pady=2)
            ctk.CTkLabel(card, text=t["name"], font=theme.f(-1, "bold"), text_color=theme.TEXT,
                         anchor="w").pack(anchor="w", padx=8, pady=(4, 0))
            if t.get("description"):
                ctk.CTkLabel(card, text=t["description"][:160], font=theme.f(-2),
                             text_color=theme.TEXT_DIM, wraplength=440, justify="left",
                             anchor="w").pack(anchor="w", padx=8, pady=(0, 4))
