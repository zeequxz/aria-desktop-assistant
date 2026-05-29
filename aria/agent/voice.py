"""
agent/voice.py - Voice input for ARIA.

Records from the microphone and transcribes using:
1. faster-whisper (local, best quality, needs ~500MB model download)
2. SpeechRecognition + Google (online, no install, less private)
3. Windows built-in speech (fallback)

The result is passed to a callback for the GUI to handle.
"""

import threading
import os
from typing import Callable, Optional

WHISPER_AVAILABLE = False
SOUNDDEVICE_AVAILABLE = False
SR_AVAILABLE = False

try:
    import faster_whisper
    WHISPER_AVAILABLE = True
except ImportError:
    pass

try:
    import sounddevice as sd
    import numpy as np
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    pass

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    pass


class VoiceRecorder:
    """Records audio and transcribes it."""

    def __init__(self, on_result: Callable[[str], None], on_error: Callable[[str], None]):
        self.on_result = on_result
        self.on_error = on_error
        self._recording = False
        self._whisper_model = None
        self._thread = None

    def start_recording(self, mode: str = "auto"):
        """Start recording. mode = 'whisper' | 'google' | 'auto'"""
        if self._recording:
            return
        self._recording = True
        self._thread = threading.Thread(
            target=self._record_and_transcribe, args=(mode,), daemon=True
        )
        self._thread.start()

    def stop_recording(self):
        self._recording = False

    def _record_and_transcribe(self, mode: str):
        if mode == "auto":
            if WHISPER_AVAILABLE and SOUNDDEVICE_AVAILABLE:
                mode = "whisper"
            elif SR_AVAILABLE:
                mode = "google"
            else:
                self.on_error("No voice input available. Install: pip install SpeechRecognition pyaudio")
                return

        if mode == "whisper":
            self._whisper_record()
        elif mode == "google":
            self._google_record()

    def _whisper_record(self):
        """Record audio then transcribe with local Whisper model."""
        if not SOUNDDEVICE_AVAILABLE:
            self.on_error("sounddevice not installed: pip install sounddevice numpy")
            return
        try:
            import sounddevice as sd
            import numpy as np

            sample_rate = 16000
            max_seconds = 30
            silence_threshold = 0.01
            silence_duration = 1.5
            frames = []
            silent_frames = 0
            frames_per_check = int(sample_rate * 0.1)

            # Record with silence detection
            with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
                while self._recording:
                    data, _ = stream.read(frames_per_check)
                    frames.append(data.copy())
                    rms = np.sqrt(np.mean(data ** 2))
                    if rms < silence_threshold:
                        silent_frames += 1
                    else:
                        silent_frames = 0
                    # Stop after silence
                    if silent_frames > (silence_duration / 0.1):
                        break
                    if len(frames) * frames_per_check / sample_rate > max_seconds:
                        break

            self._recording = False
            audio = np.concatenate(frames, axis=0).flatten()

            # Transcribe
            if self._whisper_model is None:
                self._whisper_model = faster_whisper.WhisperModel("base", device="cpu")
            segments, _ = self._whisper_model.transcribe(audio, language=None, beam_size=5)
            text = " ".join(s.text for s in segments).strip()
            if text:
                self.on_result(text)
            else:
                self.on_error("No speech detected.")
        except Exception as e:
            self.on_error(str(e))
        finally:
            self._recording = False

    def _google_record(self):
        """Use SpeechRecognition with Google's free API."""
        if not SR_AVAILABLE:
            self.on_error("SpeechRecognition not installed: pip install SpeechRecognition pyaudio")
            return
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=30)
            text = recognizer.recognize_google(audio)
            self._recording = False
            if text:
                self.on_result(text)
            else:
                self.on_error("No speech detected.")
        except Exception as e:
            self._recording = False
            self.on_error(str(e))

    @property
    def is_recording(self):
        return self._recording

    @staticmethod
    def is_available() -> tuple[bool, str]:
        if WHISPER_AVAILABLE and SOUNDDEVICE_AVAILABLE:
            return True, "whisper"
        if SR_AVAILABLE:
            return True, "google"
        return False, "none"
