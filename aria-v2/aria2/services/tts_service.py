"""services/tts_service.py - Speak replies aloud (ported from v1).

Uses pyttsx3 (offline, cross-platform) when available; degrades to a no-op if
the library or a system voice is missing. Speaking runs on a worker thread so it
never blocks the UI. Optional voice *input* (speech_recognition) is exposed via
listen() for the chat mic button.
"""

from __future__ import annotations

import threading

from aria2.core import config

try:
    import pyttsx3

    TTS_AVAILABLE = True
except Exception:  # pragma: no cover
    TTS_AVAILABLE = False

_lock = threading.Lock()


def speak(text: str) -> None:
    """Speak text aloud if TTS is enabled and available (non-blocking)."""
    if not text or not TTS_AVAILABLE or not config.get("tts_enabled", False):
        return

    def _run():
        with _lock:  # one utterance at a time
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", config.get("tts_rate", 175))
                voice = config.get("tts_voice", "")
                if voice:
                    engine.setProperty("voice", voice)
                engine.say(text[:1000])
                engine.runAndWait()
                engine.stop()
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True, name="tts").start()


def listen(timeout: int = 8) -> dict:
    """Capture speech from the microphone and return {text} or {error}.
    Optional — needs speech_recognition + a mic."""
    try:
        import speech_recognition as sr
    except Exception:
        return {"error": "speech_recognition not installed"}
    try:
        r = sr.Recognizer()
        with sr.Microphone() as source:
            audio = r.listen(source, timeout=timeout, phrase_time_limit=timeout)
        return {"text": r.recognize_google(audio)}
    except Exception as e:
        return {"error": str(e)}
