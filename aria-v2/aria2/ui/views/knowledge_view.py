"""ui/views/knowledge_view.py - Ingest documents/folders and search the KB."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from aria2.services import knowledge_service, project_service
from aria2.ui import theme
from aria2.ui.views import widgets as w


class KnowledgeView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        w.header(self, "Knowledge", "Index documents and code, then search with citations (RAG).")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=24)
        self.project_menu = ctk.CTkOptionMenu(
            bar, values=["General"], fg_color=theme.SURFACE, button_color=theme.SURFACE_2,
            command=lambda *_: self._refresh_docs(),
        )
        self.project_menu.pack(side="left")
        w.ghost_button(bar, "Ingest file", self._ingest_file, width=110).pack(side="left", padx=8)
        w.ghost_button(bar, "Ingest folder", self._ingest_folder, width=120).pack(side="left")
        self.status = ctk.CTkLabel(bar, text="", font=theme.f(-1), text_color=theme.TEXT_DIM)
        self.status.pack(side="left", padx=12)

        search = ctk.CTkFrame(self, fg_color="transparent")
        search.pack(fill="x", padx=24, pady=10)
        self.query = ctk.CTkEntry(search, placeholder_text="Search the knowledge base…",
                                  fg_color=theme.SURFACE_2, border_color=theme.BORDER)
        self.query.pack(side="left", fill="x", expand=True)
        self.query.bind("<Return>", lambda e: self._search())
        w.primary_button(search, "Search", self._search, width=100).pack(side="left", padx=8)

        from aria2.ui.views.paned_view import make_paned
        left_pane, right_pane = make_paned(self, "sidebar_knowledge_width",
                                           default_w=480, min_w=200, max_w=800,
                                           pady=(0, 16))
        docs_card = w.card(left_pane)
        docs_card.pack(fill="both", expand=True)
        ctk.CTkLabel(docs_card, text="Documents", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.docs = ctk.CTkScrollableFrame(docs_card, fg_color="transparent")
        self.docs.pack(fill="both", expand=True, padx=6, pady=6)

        res_card = w.card(right_pane)
        res_card.pack(fill="both", expand=True)
        ctk.CTkLabel(res_card, text="Results", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.results = ctk.CTkScrollableFrame(res_card, fg_color="transparent")
        self.results.pack(fill="both", expand=True, padx=6, pady=6)

    def on_show(self):
        projects = project_service.list_projects()
        self._projects = {p["name"]: p["id"] for p in projects}
        self.project_menu.configure(values=list(self._projects))
        active = next((n for n, i in self._projects.items() if i == self.app.active_project), None)
        if active:
            self.project_menu.set(active)
        self._refresh_docs()

    def _pid(self):
        return self._projects.get(self.project_menu.get(), "general")

    def _refresh_docs(self):
        for c in self.docs.winfo_children():
            c.destroy()
        for d in knowledge_service.list_documents(self._pid()):
            row = ctk.CTkFrame(self.docs, fg_color=theme.SURFACE_2, corner_radius=6)
            row.pack(fill="x", pady=2, padx=2)
            ctk.CTkLabel(row, text=f"{d['title']}  ·  {d['n_chunks']} chunks  v{d['version']}",
                         font=theme.f(-1), text_color=theme.TEXT, anchor="w").pack(
                side="left", padx=8, pady=4)
            ctk.CTkButton(row, text="✕", width=28, fg_color="transparent",
                          hover_color=theme.BORDER, text_color=theme.TEXT_FAINT,
                          command=lambda i=d["id"]: self._delete(i)).pack(side="right")

    def _delete(self, doc_id):
        knowledge_service.delete_document(doc_id)
        self._refresh_docs()

    def _ingest_file(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        self._run(lambda: knowledge_service.ingest_file(self._pid(), path), "file")

    def _ingest_folder(self):
        path = filedialog.askdirectory()
        if not path:
            return
        self._run(lambda: knowledge_service.ingest_folder(self._pid(), path), "folder")

    def _run(self, fn, what):
        self.status.configure(text=f"Ingesting {what}…")

        def worker():
            res = fn()
            msg = res.get("error") or f"Indexed {res.get('chunks', res.get('files',0))} chunks"
            kind = "error" if res.get("error") else "success"
            self.after(0, lambda m=msg, k=kind: (
                self.app.toast(m, k), self.status.configure(text=""),
                self._refresh_docs()))

        threading.Thread(target=worker, daemon=True).start()

    def _search(self):
        q = self.query.get().strip()
        for c in self.results.winfo_children():
            c.destroy()
        if not q:
            return
        hits = knowledge_service.search(q, self._pid(), limit=8)
        if not hits:
            ctk.CTkLabel(self.results, text="No matches.", font=theme.f(-1),
                         text_color=theme.TEXT_FAINT).pack(anchor="w", padx=8, pady=8)
            return
        for h in hits:
            card = ctk.CTkFrame(self.results, fg_color=theme.SURFACE_2, corner_radius=6)
            card.pack(fill="x", pady=3, padx=2)
            ctk.CTkLabel(card, text=f"{h['title']}  ·  {h['score']:.2f}",
                         font=theme.f(-2, "bold"), text_color=theme.accent(), anchor="w").pack(
                anchor="w", padx=8, pady=(6, 0))
            ctk.CTkLabel(card, text=h["text"][:400], font=theme.f(-1), text_color=theme.TEXT,
                         wraplength=420, justify="left", anchor="w").pack(
                anchor="w", padx=8, pady=(0, 6))
