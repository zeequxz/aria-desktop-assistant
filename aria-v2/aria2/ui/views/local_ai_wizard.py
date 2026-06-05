"""ui/views/local_ai_wizard.py - Guided local AI setup wizard for beginners.

A multi-step wizard that:
  1. Explains what local AI is and why it's useful
  2. Detects available RAM and recommends suitable models
  3. Shows a model picker with hardware requirements and use cases
  4. Guides through installing Ollama
  5. Shows the pull command for the chosen model
  6. Configures ARIA to use that model and tests the connection

Accessible from Settings → Providers and from the Chat empty-state onboarding.
"""

from __future__ import annotations

import subprocess
import threading
import webbrowser
from pathlib import Path

import customtkinter as ctk

from aria2.core import config
from aria2.ui import theme
from aria2.ui.views import widgets as w

_OLLAMA_URL = "https://ollama.com/download"

# Model catalogue: (id, display_name, ram_gb, speed, quality, description)
_MODELS = [
    ("llama3.2:1b",    "Llama 3.2 · 1B",    1.5, "⚡ Very fast", "★★☆",
     "Smallest usable model. Great for testing and very low-end PCs. Limited reasoning."),
    ("llama3.2:3b",    "Llama 3.2 · 3B",    2.5, "⚡ Fast",      "★★★",
     "Good all-rounder for everyday chat on any modern PC with 8 GB RAM."),
    ("llama3.1:8b",    "Llama 3.1 · 8B",    5.5, "◐ Medium",    "★★★★",
     "Best quality-to-speed balance. Recommended for 16 GB RAM systems."),
    ("qwen2.5:7b",     "Qwen 2.5 · 7B",     5.0, "◐ Medium",    "★★★★",
     "Excellent for code and multilingual tasks. Strong reasoning."),
    ("mistral:7b",     "Mistral · 7B",       5.0, "◐ Medium",    "★★★★",
     "Great general-purpose model, strong instruction following."),
    ("deepseek-r1:7b", "DeepSeek R1 · 7B",  5.0, "◐ Medium",    "★★★★",
     "Specialises in step-by-step reasoning and problem solving."),
    ("phi3:mini",      "Phi-3 Mini · 3.8B", 3.0, "⚡ Fast",      "★★★",
     "Microsoft's compact model. Very fast and surprisingly capable."),
    ("gemma2:2b",      "Gemma 2 · 2B",      1.6, "⚡ Fast",      "★★★",
     "Google's efficient small model. Smooth on low-end hardware."),
    ("llama3.1:70b",   "Llama 3.1 · 70B",  42.0, "🐢 Slow",      "★★★★★",
     "Near-GPT-4 quality. Needs 48 GB+ RAM or a powerful GPU."),
    ("deepseek-r1:14b","DeepSeek R1 · 14B", 9.0, "◐ Medium",    "★★★★★",
     "Best reasoning quality in the <15 GB range. Needs 16 GB RAM."),
    ("codellama:7b",   "Code Llama · 7B",   5.0, "◐ Medium",    "★★★★",
     "Fine-tuned on code. Best choice if you mainly write or review code."),
    ("nomic-embed-text","Nomic Embed",       0.8, "⚡ Very fast", "N/A",
     "Embedding-only model (no chat). Improves ARIA's memory recall quality."),
]


def _detect_ram_gb() -> float:
    """Best-effort RAM detection. Returns 0 on failure."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        pass
    try:
        # Windows fallback via wmic
        out = subprocess.check_output(
            ["wmic", "computersystem", "get", "TotalPhysicalMemory"],
            timeout=5, text=True)
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line) / (1024 ** 3)
    except Exception:
        pass
    return 0.0


def _ollama_installed() -> bool:
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _detect_gpu() -> tuple[bool, str]:
    """Return (has_gpu, description). Best-effort, non-blocking."""
    # NVIDIA
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=6)
        if r.returncode == 0 and r.stdout.strip():
            line = r.stdout.strip().splitlines()[0]
            return True, f"NVIDIA {line}"
    except Exception:
        pass
    # Windows generic (no nvidia-smi — might still have AMD/Intel)
    try:
        r = subprocess.run(
            ["wmic", "path", "win32_videocontroller", "get", "caption,adapterram"],
            capture_output=True, text=True, timeout=6)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and "caption" not in line.lower() and len(line) > 4:
                return True, line.split("  ")[0]
    except Exception:
        pass
    return False, ""


# ── Wizard ────────────────────────────────────────────────────────────────────

class LocalAIWizard(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Set up Local AI")
        self.geometry("700x680")
        self.configure(fg_color=theme.SURFACE)
        self.transient(parent)
        self.resizable(True, True)

        self._ram = _detect_ram_gb()
        self._has_gpu, self._gpu_desc = _detect_gpu()
        self._selected_model: str = self._recommend_model()
        self._step = 0
        self._steps = [
            self._page_welcome,
            self._page_hardware,
            self._page_model,
            self._page_install,
            self._page_pull,
            self._page_configure,
        ]

        # Header (progress bar area)
        self._hdr = ctk.CTkFrame(self, fg_color=theme.SURFACE_2, corner_radius=0)
        self._hdr.pack(fill="x")
        self._title_lbl = ctk.CTkLabel(self._hdr, text="",
                                       font=theme.f(3, "bold"), text_color=theme.TEXT)
        self._title_lbl.pack(anchor="w", padx=20, pady=(14, 4))
        self._progress = ctk.CTkProgressBar(self._hdr, height=4,
                                            fg_color=theme.BORDER,
                                            progress_color=theme.accent())
        self._progress.pack(fill="x", padx=20, pady=(0, 12))

        # Page area
        self._page_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._page_frame.pack(fill="both", expand=True, padx=4, pady=4)

        # Nav buttons
        nav = ctk.CTkFrame(self, fg_color=theme.SURFACE_2, corner_radius=0)
        nav.pack(fill="x")
        self._back_btn = w.ghost_button(nav, "← Back", self._back, width=110, height=36)
        self._back_btn.pack(side="left", padx=12, pady=10)
        self._next_btn = w.primary_button(nav, "Next →", self._next, width=110, height=36)
        self._next_btn.pack(side="right", padx=12, pady=10)

        self._render()

    def _recommend_model(self) -> str:
        ram = self._ram
        # On GPU, we can run larger models comfortably
        if getattr(self, "_has_gpu", False):
            if ram <= 0 or ram >= 8:
                return "llama3.1:8b"
            return "llama3.2:3b"
        if ram <= 0 or ram >= 16:
            return "llama3.1:8b"
        if ram >= 8:
            return "llama3.2:3b"
        return "llama3.2:1b"

    def _render(self):
        for c in self._page_frame.winfo_children():
            c.destroy()
        n = len(self._steps)
        self._progress.set((self._step + 1) / n)
        self._back_btn.configure(state="normal" if self._step > 0 else "disabled")
        self._steps[self._step]()

    def _next(self):
        if self._step < len(self._steps) - 1:
            self._step += 1
            self._render()
        else:
            self._finish()

    def _back(self):
        if self._step > 0:
            self._step -= 1
            self._render()

    def _finish(self):
        self.destroy()

    # ── Page helpers ──────────────────────────────────────────────────────────

    def _h(self, text: str, subtitle: str = ""):
        self._title_lbl.configure(text=text)
        if subtitle:
            ctk.CTkLabel(self._page_frame, text=subtitle,
                         font=theme.f(-1), text_color=theme.TEXT_DIM,
                         wraplength=640, justify="left").pack(
                anchor="w", padx=20, pady=(12, 0))

    def _para(self, text: str):
        ctk.CTkLabel(self._page_frame, text=text, font=theme.f(0),
                     text_color=theme.TEXT, wraplength=640,
                     justify="left", anchor="w").pack(anchor="w", padx=20, pady=(10, 0))

    def _card(self, **kw) -> ctk.CTkFrame:
        c = ctk.CTkFrame(self._page_frame, fg_color=theme.SURFACE,
                         corner_radius=10, border_width=1,
                         border_color=theme.BORDER, **kw)
        c.pack(fill="x", padx=20, pady=6)
        return c

    def _code(self, text: str):
        box = ctk.CTkTextbox(self._page_frame, height=34, fg_color=theme.SURFACE,
                             font=(theme.MONO, theme.font_size()),
                             border_width=0, activate_scrollbars=False)
        box.pack(fill="x", padx=20, pady=(4, 0))
        box.insert("1.0", text)
        box.configure(state="disabled")

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _page_welcome(self):
        self._h("Welcome to local AI", "Step 1 of 6")
        self._next_btn.configure(text="Let's go →")

        ctk.CTkLabel(self._page_frame, text="🤖",
                     font=(theme.FONT, 56)).pack(pady=(30, 8))
        ctk.CTkLabel(self._page_frame, text="Run AI directly on your computer",
                     font=theme.f(5, "bold"), text_color=theme.TEXT).pack()
        ctk.CTkLabel(self._page_frame, text="No API key. No monthly bill. No data leaving your machine.",
                     font=theme.f(0), text_color=theme.TEXT_DIM).pack(pady=(4, 20))

        for icon, title, body in [
            ("🔒", "Private by default",
             "Your conversations never leave your computer."),
            ("💸", "Completely free",
             "No usage fees, no subscriptions — the model runs locally."),
            ("✈️", "Works offline",
             "No internet needed once the model is downloaded."),
            ("⚡", "Gets smarter over time",
             "Upgrade to larger models as your hardware improves."),
        ]:
            c = self._card()
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=10)
            ctk.CTkLabel(row, text=icon, font=(theme.FONT, 24), width=40).pack(side="left")
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", padx=8)
            ctk.CTkLabel(col, text=title, font=theme.f(0, "bold"),
                         text_color=theme.TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(col, text=body, font=theme.f(-1),
                         text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w")

    def _page_hardware(self):
        self._h("Your hardware", "Step 2 of 6 — we'll recommend the right model")
        self._next_btn.configure(text="Next →")

        # RAM reading
        c = self._card()
        ctk.CTkLabel(c, text="Detected system RAM",
                     font=theme.f(-1), text_color=theme.TEXT_DIM).pack(
            anchor="w", padx=14, pady=(10, 0))
        ram_text = f"{self._ram:.0f} GB" if self._ram > 0 else "Unable to detect"
        ctk.CTkLabel(c, text=ram_text,
                     font=theme.f(8, "bold"), text_color=theme.accent()).pack(
            anchor="w", padx=14)
        ctk.CTkLabel(c, text=self._tier_desc(), font=theme.f(-1),
                     text_color=theme.TEXT_DIM, wraplength=580, justify="left").pack(
            anchor="w", padx=14, pady=(0, 10))

        # Tier table
        ctk.CTkLabel(self._page_frame, text="What can your computer run?",
                     font=theme.f(0, "bold"), text_color=theme.TEXT).pack(
            anchor="w", padx=20, pady=(16, 6))

        for ram, tier, desc, colour in [
            (4,  "4 GB RAM",  "Tiny models only (1B–2B). Basic chat works.", theme.WARN),
            (8,  "8 GB RAM",  "Small models (3B–7B). Good daily assistant.", theme.SUCCESS),
            (16, "16 GB RAM", "Full quality models (7B–13B). Great experience.", theme.SUCCESS),
            (32, "32 GB+ RAM", "Large models (30B+). Near-cloud quality.", theme.accent()),
        ]:
            row_c = self._card()
            row = ctk.CTkFrame(row_c, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=8)
            active = self._ram > 0 and self._ram >= ram - 1
            ctk.CTkLabel(row, text=tier, font=theme.f(-1, "bold"), width=120,
                         text_color=colour if active else theme.TEXT_FAINT).pack(side="left")
            ctk.CTkLabel(row, text=desc, font=theme.f(-1),
                         text_color=theme.TEXT if active else theme.TEXT_FAINT).pack(side="left", padx=8)
            if active and (self._ram > 0 and self._ram < ram + 8):
                ctk.CTkLabel(row, text="← You are here", font=theme.f(-2),
                             text_color=theme.accent()).pack(side="right", padx=8)

        # GPU card
        gpu_card = self._card()
        gpu_row = ctk.CTkFrame(gpu_card, fg_color="transparent")
        gpu_row.pack(fill="x", padx=12, pady=10)
        if self._has_gpu:
            ctk.CTkLabel(gpu_row, text="🎮", font=(theme.FONT, 22), width=36).pack(side="left")
            col = ctk.CTkFrame(gpu_row, fg_color="transparent")
            col.pack(side="left", padx=8)
            ctk.CTkLabel(col, text="GPU detected — great news!",
                         font=theme.f(0, "bold"), text_color=theme.SUCCESS,
                         anchor="w").pack(anchor="w")
            ctk.CTkLabel(col, text=f"{self._gpu_desc}",
                         font=theme.f(-1), text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w")
            ctk.CTkLabel(col, text="Models will run 5–20× faster on GPU than on RAM alone. "
                                   "Ollama uses your GPU automatically when available.",
                         font=theme.f(-1), text_color=theme.TEXT_DIM,
                         wraplength=500, justify="left").pack(anchor="w", pady=(2, 0))
        else:
            ctk.CTkLabel(gpu_row, text="🖥", font=(theme.FONT, 22), width=36).pack(side="left")
            col = ctk.CTkFrame(gpu_row, fg_color="transparent")
            col.pack(side="left", padx=8)
            ctk.CTkLabel(col, text="No dedicated GPU detected",
                         font=theme.f(0, "bold"), text_color=theme.TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(col,
                         text="Models will run on your CPU/RAM — they work fine for everyday use, "
                              "just at reduced speed. Larger models (70B+) need a powerful GPU "
                              "(6 GB+ VRAM) to run comfortably; they can run on RAM but will be "
                              "very slow. Stick to the recommended model for your RAM tier.",
                         font=theme.f(-1), text_color=theme.TEXT_DIM,
                         wraplength=500, justify="left").pack(anchor="w", pady=(2, 0))

    def _tier_desc(self) -> str:
        if self._ram <= 0:
            return "Could not detect RAM automatically. Choose a model on the next page."
        if self._ram < 4:
            return "Your system has very little RAM. Tiny models may work but will be slow."
        if self._ram < 8:
            return "You can run small 1B–3B models smoothly."
        if self._ram < 16:
            return "You can run 3B–7B models comfortably — a good everyday experience."
        if self._ram < 32:
            return "You can run 7B–13B models at full quality — excellent performance."
        return "You can run very large models (30B+) with near-cloud quality."

    def _page_model(self):
        self._h("Choose your model", "Step 3 of 6")

        if self._has_gpu:
            ctk.CTkLabel(self._page_frame,
                         text=f"🎮  GPU detected — larger models are unlocked for you. "
                              "All models will run faster than the RAM estimates suggest.",
                         font=theme.f(-1), text_color=theme.SUCCESS,
                         wraplength=640, justify="left").pack(anchor="w", padx=20, pady=(10, 2))
        else:
            ctk.CTkLabel(self._page_frame,
                         text='No GPU detected. Models can still run on RAM but will be slow. '
                              'A GPU with 6+ GB VRAM unlocks larger models and speeds them up 5-20x.',
                         font=theme.f(-1), text_color=theme.TEXT_DIM,
                         wraplength=640, justify="left").pack(anchor="w", padx=20, pady=(10, 2))

        ctk.CTkLabel(self._page_frame, text="✦ Recommended for your hardware",
                     font=theme.f(-2, "bold"), text_color=theme.accent()).pack(
            anchor="w", padx=20, pady=(6, 2))

        self._model_var = ctk.StringVar(value=self._selected_model)
        shown = 0
        for mid, name, ram, speed, quality, desc in _MODELS:
            fits = self._ram <= 0 or ram <= self._ram * 0.85
            recommended = mid == self._recommend_model()
            c = self._card()
            if recommended:
                c.configure(border_color=theme.accent())
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=8)
            rb = ctk.CTkRadioButton(row, text="", variable=self._model_var,
                                    value=mid, width=28,
                                    command=lambda m=mid: self._pick_model(m))
            rb.pack(side="left")
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=6)
            head_row = ctk.CTkFrame(info, fg_color="transparent")
            head_row.pack(fill="x")
            ctk.CTkLabel(head_row, text=name, font=theme.f(0, "bold"),
                         text_color=theme.TEXT if fits else theme.TEXT_FAINT).pack(side="left")
            ctk.CTkLabel(head_row, text=f"  {ram} GB · {speed} · {quality}",
                         font=theme.f(-2),
                         text_color=theme.TEXT_DIM if fits else theme.TEXT_FAINT).pack(side="left")
            if recommended:
                ctk.CTkLabel(head_row, text=" ✦ Recommended", font=theme.f(-2, "bold"),
                             text_color=theme.accent()).pack(side="left", padx=4)
            if not fits:
                note = " ⚠ needs GPU or more RAM" if self._has_gpu else " ⚠ needs more RAM"
                ctk.CTkLabel(head_row, text=note, font=theme.f(-2),
                             text_color=theme.WARN).pack(side="right", padx=4)
            ctk.CTkLabel(info, text=desc, font=theme.f(-1),
                         text_color=theme.TEXT_DIM if fits else theme.TEXT_FAINT,
                         wraplength=520, justify="left", anchor="w").pack(anchor="w")
            shown += 1

    def _pick_model(self, model_id: str):
        self._selected_model = model_id

    def _page_install(self):
        self._h("Install Ollama", "Step 4 of 6")

        installed = _ollama_installed()
        if installed:
            c = self._card()
            ctk.CTkLabel(c, text="✓  Ollama is already installed",
                         font=theme.f(0, "bold"), text_color=theme.SUCCESS).pack(
                anchor="w", padx=16, pady=12)
            ctk.CTkLabel(c, text="Great — you can skip straight to the next step.",
                         font=theme.f(-1), text_color=theme.TEXT_DIM).pack(
                anchor="w", padx=16, pady=(0, 10))
            return

        ctk.CTkLabel(self._page_frame, text="🦙",
                     font=(theme.FONT, 48)).pack(pady=(20, 6))
        ctk.CTkLabel(self._page_frame, text="Ollama runs AI models locally",
                     font=theme.f(3, "bold"), text_color=theme.TEXT).pack()
        ctk.CTkLabel(self._page_frame,
                     text="It's free, open-source, and takes about 2 minutes to install.",
                     font=theme.f(-1), text_color=theme.TEXT_DIM).pack(pady=(4, 16))

        for step, body in [
            ("1. Download Ollama",
             "Click the button below to open the Ollama website. "
             "Download the Windows installer and run it — it's a standard .exe setup wizard."),
            ("2. Ollama runs in the background",
             "Once installed, Ollama starts automatically as a background service "
             "at http://localhost:11434. You don't need to keep any window open."),
            ("3. Come back here",
             "After installing, click Next to continue — ARIA will guide you through "
             "downloading a model."),
        ]:
            c = self._card()
            ctk.CTkLabel(c, text=step, font=theme.f(0, "bold"),
                         text_color=theme.TEXT).pack(anchor="w", padx=14, pady=(10, 2))
            ctk.CTkLabel(c, text=body, font=theme.f(-1), text_color=theme.TEXT_DIM,
                         wraplength=580, justify="left").pack(
                anchor="w", padx=14, pady=(0, 10))

        w.primary_button(self._page_frame, "⬇  Download Ollama (ollama.com)",
                         lambda: webbrowser.open(_OLLAMA_URL),
                         height=42).pack(pady=16)

    def _page_pull(self):
        mid = self._selected_model
        name = next((n for i, n, *_ in _MODELS if i == mid), mid)
        self._h(f"Download {name}", "Step 5 of 6")

        self._para(f"Run the following command in a terminal (Command Prompt or PowerShell) "
                   f"to download {name}. The file is typically 2–8 GB so may take a few minutes.")

        cmd = f"ollama pull {mid}"
        self._code(cmd)

        c = self._card()
        btns = ctk.CTkFrame(c, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=12)
        w.ghost_button(btns, "📋  Copy command", lambda: self._copy(cmd), width=160).pack(side="left")
        w.ghost_button(btns, "▶  Open Terminal", self._open_terminal, width=160).pack(
            side="left", padx=8)

        # Live status
        self._pull_status = ctk.CTkLabel(
            self._page_frame, text="", font=theme.f(-1), text_color=theme.TEXT_DIM)
        self._pull_status.pack(anchor="w", padx=20, pady=(8, 0))

        w.ghost_button(self._page_frame, "⟳  Check if model is ready",
                       lambda: self._check_model(mid), width=200, height=32).pack(
            anchor="w", padx=20, pady=8)

        self._para("Once the download is complete, click Next to configure ARIA.")

    def _copy(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.toast_tmp("Copied to clipboard")
        except Exception:
            pass

    def toast_tmp(self, msg: str):
        lbl = ctk.CTkLabel(self._page_frame, text=f"✓ {msg}",
                           font=theme.f(-1), text_color=theme.SUCCESS)
        lbl.pack(anchor="w", padx=20)
        self.after(2000, lbl.destroy)

    def _open_terminal(self):
        try:
            import subprocess as sp
            sp.Popen(["cmd.exe"], creationflags=0x00000010)  # CREATE_NEW_CONSOLE
        except Exception:
            pass

    def _check_model(self, mid: str):
        def worker():
            try:
                import subprocess as sp, json
                r = sp.run(["ollama", "list"], capture_output=True, text=True, timeout=8)
                if mid.split(":")[0] in r.stdout:
                    self.after(0, lambda: self._pull_status.configure(
                        text=f"✓ {mid} is downloaded and ready!", text_color=theme.SUCCESS))
                else:
                    self.after(0, lambda: self._pull_status.configure(
                        text="Not found yet — the download may still be in progress.",
                        text_color=theme.WARN))
            except Exception as e:
                self.after(0, lambda: self._pull_status.configure(
                    text=f"Could not check: {e}", text_color=theme.DANGER))
        threading.Thread(target=worker, daemon=True).start()
        self._pull_status.configure(text="Checking…", text_color=theme.TEXT_DIM)

    def _page_configure(self):
        mid = self._selected_model
        name = next((n for i, n, *_ in _MODELS if i == mid), mid)
        self._h("Configure ARIA", "Step 6 of 6")
        self._next_btn.configure(text="Finish ✓")

        c = self._card()
        ctk.CTkLabel(c, text="Ready to configure ARIA",
                     font=theme.f(1, "bold"), text_color=theme.TEXT).pack(
            anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(c, text=f"ARIA will be set to use {name} via Ollama (localhost:11434).",
                     font=theme.f(-1), text_color=theme.TEXT_DIM).pack(
            anchor="w", padx=14, pady=(0, 8))
        self._applied = False
        apply_btn = w.primary_button(c, f"⚙  Apply — use {name}",
                                     lambda: self._apply(mid, name, apply_btn),
                                     height=40)
        apply_btn.pack(anchor="w", padx=14, pady=(0, 14))

        self._apply_status = ctk.CTkLabel(c, text="", font=theme.f(-1),
                                          text_color=theme.TEXT_DIM)
        self._apply_status.pack(anchor="w", padx=14, pady=(0, 8))

        self._para("After clicking Apply, head to the Chat tab and send a message. "
                   "The first response may take 10–30 seconds while the model loads "
                   "— after that it will be much faster.")

        ctk.CTkLabel(self._page_frame, text="💡 Tips", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=20, pady=(16, 4))
        for tip in [
            "Keep Ollama running in the background — ARIA connects to it automatically.",
            "You can switch models any time in Settings → Providers.",
            "Download a bigger model later with: ollama pull llama3.1:8b",
            "Add 'nomic-embed-text' model to improve ARIA's memory quality.",
        ]:
            ctk.CTkLabel(self._page_frame, text=f"• {tip}", font=theme.f(-1),
                         text_color=theme.TEXT_DIM, wraplength=640, justify="left",
                         anchor="w").pack(anchor="w", padx=20, pady=1)

    def _apply(self, mid: str, name: str, btn):
        s = config.load()
        s["provider"] = "local"
        s["ollama_model"] = mid
        s["ollama_url"] = "http://localhost:11434"
        config.save(s)
        self._applied = True
        btn.configure(state="disabled")
        self._apply_status.configure(
            text=f"✓ Done! ARIA is now configured to use {name}.",
            text_color=theme.SUCCESS)
        self.toast_tmp("Settings applied")


def open_wizard(parent):
    LocalAIWizard(parent)
