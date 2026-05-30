"""
agent/tts.py - Text-to-speech (ARIA speaks responses aloud).

Uses pyttsx3, which drives the OS's built-in speech engine (SAPI5 on Windows) —
fully offline, no API key, and bundles cleanly with PyInstaller.

Design:
  * A single background worker thread owns one engine and pulls text off a queue,
    so calls never block the GUI and never collide (pyttsx3 is not thread-safe
    if you call runAndWait() from multiple threads).
  * speak() enqueues; stop() clears the queue and interrupts the current phrase.
  * Degrades gracefully: if pyttsx3 isn't installed, is_available() is False and
    speak() is a no-op.
"""

import threading
import queue

try:
    import pyttsx3

    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False


class _TTSEngine:
    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._started = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return TTS_AVAILABLE

    def _ensure_worker(self):
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()
            self._started = True

    def _worker(self):
        # The engine is created inside the worker thread that uses it.
        try:
            engine = pyttsx3.init()
        except Exception:
            return
        while True:
            item = self._queue.get()
            if item is None:
                continue
            text, rate, voice_id = item
            try:
                if rate:
                    engine.setProperty("rate", int(rate))
                if voice_id:
                    engine.setProperty("voice", voice_id)
                engine.say(text)
                engine.runAndWait()
            except Exception:
                # A failed phrase shouldn't kill the worker.
                try:
                    engine.stop()
                except Exception:
                    pass

    def speak(self, text: str, rate: int = None, voice_id: str = None):
        """Queue text to be spoken. No-op if TTS isn't available or text empty."""
        if not TTS_AVAILABLE or not text or not text.strip():
            return
        self._ensure_worker()
        self._queue.put((text.strip(), rate, voice_id))

    def stop(self):
        """Drop anything queued. (The current phrase finishes; pyttsx3 can't be
        interrupted cleanly cross-thread without risking a crash.)"""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def list_voices(self):
        """Return [(id, name)] of installed system voices, or [] if none."""
        if not TTS_AVAILABLE:
            return []
        try:
            engine = pyttsx3.init()
            voices = engine.getProperty("voices")
            out = [(v.id, getattr(v, "name", v.id)) for v in voices]
            try:
                engine.stop()
            except Exception:
                pass
            return out
        except Exception:
            return []


# Module-level singleton used by the app.
ENGINE = _TTSEngine()


def is_available() -> bool:
    return ENGINE.is_available()


def speak(text: str, rate: int = None, voice_id: str = None):
    ENGINE.speak(text, rate=rate, voice_id=voice_id)


def stop():
    ENGINE.stop()


def list_voices():
    return ENGINE.list_voices()
