"""services/ollama_model_manager.py - Multi-model Ollama lifecycle manager.

Tracks which models are loaded, auto-loads the model for the active agent,
unloads models that have been idle, and frees resources when memory is
getting full.

API surface used:
  GET  /api/ps       — list currently loaded models + their VRAM use
  GET  /api/tags     — list all installed models + sizes
  POST /api/generate — load a model (keep_alive>0) or unload it (keep_alive=0)
"""

from __future__ import annotations

import threading
import time

from aria2.core import config
from aria2.core.events import bus

_MIN_FREE_VRAM_MB = 512   # if less than this is free, unload the LRU model
_CHECK_INTERVAL_S = 60    # housekeeping every 60 s


class OllamaModelManager:
    def __init__(self):
        self._lock      = threading.RLock()
        self._last_used: dict[str, float] = {}   # model → unix timestamp
        self._running   = False
        self._thread: threading.Thread | None = None

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ollama-mgr")
        self._thread.start()

    def stop(self):
        self._running = False

    def ensure_model(self, model: str, idle_minutes: int | None = None):
        """Load `model` (if not already loaded) and extend its keep-alive.
        Runs in a background thread so callers are never blocked."""
        threading.Thread(
            target=self._load_model,
            args=(model, idle_minutes),
            daemon=True, name=f"load-{model}").start()

    def ping(self, model: str):
        """Record that `model` was just used. Prevents idle-unload."""
        with self._lock:
            self._last_used[model] = time.time()

    def unload(self, model: str):
        """Explicitly unload a model and release its VRAM immediately."""
        threading.Thread(
            target=self._unload_model,
            args=(model,),
            daemon=True, name=f"unload-{model}").start()

    # ── Ollama API helpers ─────────────────────────────────────────────────────

    def _url(self) -> str:
        return config.get("ollama_url", "http://localhost:11434").rstrip("/")

    def _post(self, path: str, body: dict, timeout: int = 180) -> dict:
        try:
            import requests
            r = requests.post(f"{self._url()}{path}", json=body, timeout=timeout)
            r.raise_for_status()
            return r.json() if r.text.strip() else {}
        except Exception:
            return {}

    def _get(self, path: str, timeout: int = 10) -> dict:
        try:
            import requests
            r = requests.get(f"{self._url()}{path}", timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    def get_loaded(self) -> list[dict]:
        """Return models currently loaded in Ollama with their VRAM sizes."""
        return self._get("/api/ps").get("models", [])

    def get_installed(self) -> list[dict]:
        """Return all installed models with name + size."""
        raw = self._get("/api/tags").get("models", [])
        out = []
        for m in raw:
            name  = m.get("name", "")
            size  = m.get("size", 0)
            vram  = next((lm.get("size_vram", 0) for lm in self.get_loaded()
                          if lm.get("model") == name), None)
            out.append({"name": name, "size_mb": size // 1024 // 1024,
                        "loaded": vram is not None})
        return out

    def total_vram_mb(self) -> tuple[int, int]:
        """Return (used_mb, estimate of free_mb). Best-effort."""
        loaded = self.get_loaded()
        used = sum(m.get("size_vram", 0) or m.get("size", 0)
                   for m in loaded) // 1024 // 1024
        try:
            import subprocess, re
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                parts = r.stdout.strip().split(", ")
                total, free = int(parts[0]), int(parts[1])
                return used, free
        except Exception:
            pass
        return used, -1   # -1 = unknown

    # ── Model load / unload ───────────────────────────────────────────────────

    def _load_model(self, model: str, idle_minutes: int | None = None):
        s = config.load()
        idle_min = idle_minutes or int(s.get("ollama_idle_unload_min", 10))
        keep = f"{idle_min}m"
        self._post("/api/generate",
                   {"model": model, "prompt": "", "keep_alive": keep},
                   timeout=180)
        with self._lock:
            self._last_used[model] = time.time()
        bus.publish("ollama.model.loaded", {"model": model})

    def _unload_model(self, model: str):
        self._post("/api/generate",
                   {"model": model, "prompt": "", "keep_alive": 0},
                   timeout=30)
        with self._lock:
            self._last_used.pop(model, None)
        bus.publish("ollama.model.unloaded", {"model": model})

    # ── Housekeeping loop ─────────────────────────────────────────────────────

    def _loop(self):
        # Wait for app to settle, then load the default model.
        for _ in range(30):
            if not self._running: return
            time.sleep(0.1)
        self._load_default()

        while self._running:
            for _ in range(_CHECK_INTERVAL_S):
                if not self._running: return
                time.sleep(1)
            try:
                self._housekeep()
            except Exception:
                pass

    def _load_default(self):
        s = config.load()
        if s.get("provider") != "local":
            return
        model = s.get("ollama_model", "llama3")
        self._load_model(model)

    def _housekeep(self):
        s = config.load()
        idle_min  = int(s.get("ollama_idle_unload_min", 10))
        now       = time.time()
        loaded    = {m["model"]: m for m in self.get_loaded()}
        _, free   = self.total_vram_mb()

        with self._lock:
            usage = dict(self._last_used)

        # Unload models that have been idle too long.
        for name in list(loaded):
            last = usage.get(name, 0)
            if last and (now - last) > idle_min * 60:
                self._unload_model(name)
                bus.publish("ollama.model.idle_unloaded", {"model": name})

        # Unload LRU model if VRAM is getting tight.
        if free >= 0 and free < _MIN_FREE_VRAM_MB and usage:
            lru = min(usage, key=usage.get)
            self._unload_model(lru)
            bus.publish("ollama.model.pressure_unloaded", {"model": lru})


# Process-wide singleton.
model_manager = OllamaModelManager()
