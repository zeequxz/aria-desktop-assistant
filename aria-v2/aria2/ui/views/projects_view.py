"""ui/views/projects_view.py - Project hub (Claude-Code-style).

Left: the list of projects (each row has a ⋯ menu: rename / edit / pin / archive
/ delete). Right: a workspace header with live counts, and the selected project's
own conversation surface embedded inline — so a project's chats open *here*, not
in the standalone Chat tab.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from aria2.services import project_service
from aria2.ui import theme
from aria2.ui.views import widgets as w
from aria2.ui.views.chat_view import ChatView


class ProjectsView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.selected: str | None = None
        self._show_archived = False
        w.header(self, "Projects", "Each project is a workspace with its own chats, "
                                   "knowledge, and working folder.")

        from aria2.ui.views.paned_view import make_paned
        left, right = make_paned(self, "sidebar_projects_width",
                                 default_w=220, min_w=160, max_w=420,
                                 left_kwargs={"fg_color": theme.SIDEBAR})

        # ── Pane 1: projects list ────────────────────────────────────────────
        w.primary_button(left, "+  New project", self._new, height=34).pack(
            fill="x", padx=10, pady=10)
        self.list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list.pack(fill="both", expand=True)
        self.arch_toggle = w.ghost_button(left, "Show archived", self._toggle_archived,
                                          height=28, fg_color="transparent")
        self.arch_toggle.pack(fill="x", padx=10, pady=8)

        # ── Pane 2: workspace (counts header + embedded conversation) ────────
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.counts_bar = ctk.CTkFrame(right, fg_color="transparent")
        self.counts_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.conv = ChatView(right, self.app, project_id="general", enable_drop=False)
        self.conv.grid(row=1, column=0, sticky="nsew")

    # ── Data ────────────────────────────────────────────────────────────────────

    def on_show(self):
        self._refresh_list()
        projects = project_service.list_projects(include_archived=self._show_archived)
        target = self.selected or self.app.active_project
        if not any(p["id"] == target for p in projects):
            target = projects[0]["id"] if projects else None
        if not target:
            return
        # If we're already showing this project, only refresh the cheap header —
        # don't rebuild the embedded conversation on every tab switch.
        if target == self.selected and self.conv.project_id == target:
            self._render_counts(project_service.get(target))
        else:
            self._select(target)

    def _refresh_list(self):
        for c in self.list.winfo_children():
            c.destroy()
        for p in project_service.list_projects(include_archived=self._show_archived):
            active = p["id"] == self.selected
            row = ctk.CTkFrame(self.list,
                               fg_color=theme.accent_soft() if active else "transparent",
                               corner_radius=6)
            row.pack(fill="x", padx=6, pady=1)
            row.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(1, weight=0, minsize=28)
            label = ("📌 " if p["pinned"] else "") + ("🗄 " if p["archived"] else "🗂 ") + p["name"]
            btn = ctk.CTkButton(
                row, text=label, anchor="w", height=34, corner_radius=6,
                fg_color="transparent", hover_color=theme.HOVER,
                text_color=theme.TEXT if active else theme.TEXT_DIM,
                font=theme.f(-1), command=lambda i=p["id"]: self._select(i))
            btn.grid(row=0, column=0, sticky="ew")
            ctk.CTkButton(row, text="⋯", width=28, height=30, fg_color="transparent",
                          hover_color=theme.HOVER, text_color=theme.TEXT_FAINT,
                          font=theme.f(0), command=lambda pp=p: self._project_menu(pp)
                          ).grid(row=0, column=1, sticky="e")
            for wdg in (row, btn):
                wdg.bind("<Button-3>", lambda e, pp=p: self._project_menu(pp, e))

    def _toggle_archived(self):
        self._show_archived = not self._show_archived
        self.arch_toggle.configure(
            text="Hide archived" if self._show_archived else "Show archived")
        self._refresh_list()

    def _select(self, pid: str):
        self.selected = pid
        p = project_service.get(pid)
        if not p:
            return
        self.app.active_project = pid  # for knowledge/automations scoping
        self._refresh_list()
        self._render_counts(p)
        self.conv.set_project(pid)  # retarget the embedded conversation

    # ── Counts header ─────────────────────────────────────────────────────────────

    def _render_counts(self, p: dict):
        for c in self.counts_bar.winfo_children():
            c.destroy()
        c = project_service.counts(p["id"])
        for icon, label, n, view in (("💬", "Chats", c["chats"], None),
                                     ("📚", "Knowledge", c["documents"], "knowledge"),
                                     ("⏱", "Automations", c["automations"], "automations")):
            chip = ctk.CTkFrame(self.counts_bar, fg_color=theme.SURFACE_2, corner_radius=10)
            chip.pack(side="left", padx=(0, 8))
            cmd = (lambda v=view: self._goto(v)) if view else None
            ctk.CTkButton(chip, text=f"{icon} {n} {label}", height=30, width=120,
                          fg_color="transparent",
                          hover_color=theme.HOVER if view else theme.SURFACE_2,
                          text_color=theme.TEXT, font=theme.f(-1), command=cmd
                          ).pack(padx=2, pady=2)
        if p["folder"]:
            ctk.CTkLabel(self.counts_bar, text=f"📁 {p['folder']}", font=theme.f(-2),
                         text_color=theme.TEXT_FAINT).pack(side="left", padx=10)

        # Trust level selector — sets the default mode for chats in this project.
        trust_chip = ctk.CTkFrame(self.counts_bar, fg_color="transparent")
        trust_chip.pack(side="right", padx=4)
        ctk.CTkLabel(trust_chip, text="Trust:", font=theme.f(-2),
                     text_color=theme.TEXT_FAINT).pack(side="left")
        _TRUST = {"ask": "🙋 Ask", "accept": "✏️ Accept",
                  "auto": "⚡ Auto", "plan": "📋 Plan"}
        trust_menu = ctk.CTkOptionMenu(
            trust_chip, values=list(_TRUST.values()), width=120, height=28,
            fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2,
            font=theme.f(-1),
            command=lambda lbl, pid=p["id"]: self._set_trust(pid, lbl))
        cur = _TRUST.get(p.get("trust_level", "ask"), "🙋 Ask")
        trust_menu.set(cur)
        trust_menu.pack(side="left", padx=4)
        w.add_tooltip(trust_menu,
                      "Default mode for chats in this project.\n"
                      "Ask: confirm all  ·  Accept: auto file edits  "
                      "·  Auto: allow all  ·  Plan: plan only")

    def _set_trust(self, project_id: str, label: str):
        _REV = {"🙋 Ask": "ask", "✏️ Accept": "accept",
                "⚡ Auto": "auto", "📋 Plan": "plan"}
        project_service.set_trust(project_id, _REV.get(label, "ask"))

    def _goto(self, view: str):
        from aria2.core import config
        if self.selected:
            self.app.active_project = self.selected
            config.set_key("active_project", self.selected)
        self.app.show(view)

    # ── Project menu / actions ─────────────────────────────────────────────────────

    def _project_menu(self, p: dict, event=None):
        is_default = p["id"] == "general"
        m = tk.Menu(self, tearoff=0, bg=theme.SURFACE_2, fg=theme.TEXT,
                    activebackground=theme.accent(), activeforeground="#ffffff", bd=0)
        m.add_command(label="Rename", command=lambda: self._rename(p))
        m.add_command(label="Edit…", command=lambda: self._edit(p))
        m.add_command(label="Unpin" if p["pinned"] else "Pin", command=lambda: self._pin(p))
        if not is_default:
            m.add_command(label="Unarchive" if p["archived"] else "Archive",
                          command=lambda: self._archive(p))
        m.add_separator()
        m.add_command(label="Delete", command=lambda: self._delete(p),
                      state="disabled" if is_default else "normal")
        try:
            if event is not None:
                m.tk_popup(event.x_root, event.y_root)
            else:
                m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            m.grab_release()

    def _rename(self, p: dict):
        dlg = ctk.CTkInputDialog(text="New project name:", title="Rename project")
        name = dlg.get_input()
        if name and name.strip():
            project_service.update(p["id"], {"name": name.strip()})
            self._refresh_list()
            if self.selected == p["id"]:
                self._select(p["id"])

    def _pin(self, p: dict):
        project_service.set_pinned(p["id"], not p["pinned"])
        self._refresh_list()

    def _new(self):
        _ProjectEditDialog(self, None, self._on_saved)

    def _edit(self, p: dict):
        _ProjectEditDialog(self, p, self._on_saved)

    def _on_saved(self, data: dict, pid: str | None):
        if pid:
            project_service.update(pid, data)
        else:
            created = project_service.create(data["name"], data["folder"], data["goals"])
            pid = created["id"]
        self.selected = pid
        self._refresh_list()
        self._select(pid)
        self.app.toast("Project saved", "success")

    def _archive(self, p: dict):
        res = project_service.archive(p["id"], not p["archived"])
        if res.get("error"):
            messagebox.showinfo("Archive", res["error"], parent=self)
            return
        self.selected = None
        self.on_show()

    def _delete(self, p: dict):
        if not messagebox.askyesno(
                "Delete project",
                f"Delete “{p['name']}” and all its chats?\nThis cannot be undone.",
                icon="warning", parent=self):
            return
        res = project_service.delete(p["id"])
        if res.get("error"):
            messagebox.showinfo("Delete", res["error"], parent=self)
            return
        self.selected = None
        self.on_show()


class _ProjectEditDialog(ctk.CTkToplevel):
    """Modal editor for a project's name / folder / goals (create or edit)."""

    def __init__(self, parent, project: dict | None, on_save):
        super().__init__(parent)
        self._on_save = on_save
        self._pid = project["id"] if project else None
        self.title("Edit project" if project else "New project")
        self.geometry("520x460")
        self.configure(fg_color=theme.SURFACE)
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text=self.title(), font=theme.f(2, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=18, pady=(16, 8))
        self.name_f, self.name_e = w.labeled_entry(self, "Name",
                                                   project["name"] if project else "")
        self.name_f.pack(fill="x", padx=18, pady=4)

        frow = ctk.CTkFrame(self, fg_color="transparent")
        frow.pack(fill="x", padx=18, pady=4)
        self.folder_f, self.folder_e = w.labeled_entry(frow, "Working folder",
                                                       project["folder"] if project else "")
        self.folder_f.pack(side="left", fill="x", expand=True)
        w.ghost_button(frow, "Browse", self._browse, width=80, height=30).pack(
            side="left", padx=(8, 0), pady=(18, 0))

        ctk.CTkLabel(self, text="Goals / context", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(anchor="w", padx=18, pady=(10, 2))
        self.goals = ctk.CTkTextbox(self, height=170, fg_color=theme.SURFACE_2,
                                    font=theme.f(0), wrap="word")
        self.goals.pack(fill="both", expand=True, padx=18)
        if project:
            self.goals.insert("1.0", project["goals"] or "")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=14)
        w.primary_button(btns, "Save", self._save, width=100).pack(side="right")
        w.ghost_button(btns, "Cancel", self._close, width=90).pack(side="right", padx=8)

    def _close(self):
        """Release the window grab before destroying to prevent the CTk black-box
        phantom that appears on Windows when destroy() is called while a grab is held."""
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()          # hide immediately so no black flash
        self.after(10, self.destroy)

    def _browse(self):
        path = filedialog.askdirectory(parent=self)
        if path:
            self.folder_e.delete(0, "end")
            self.folder_e.insert(0, path)

    def _save(self):
        data = {
            "name": self.name_e.get().strip() or "Project",
            "folder": self.folder_e.get().strip(),
            "goals": self.goals.get("1.0", "end").strip(),
        }
        on_save = self._on_save
        pid = self._pid
        parent = self.master
        self._close()
        # Run the save on the (surviving) parent: an after() scheduled on this
        # dialog is dropped when the dialog is destroyed a few ms later — which is
        # why newly-created projects never appeared in the list.
        parent.after(30, lambda: on_save(data, pid))
