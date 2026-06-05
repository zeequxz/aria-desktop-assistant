"""core/secrets.py - Encrypt secret fields at rest.

On Windows we use DPAPI (CryptProtectData) bound to the current user so the
config file never contains plaintext API keys. On other platforms we fall back
to base64 obfuscation with a clear marker (not real encryption) so the app
still runs cross-platform during development.
"""

from __future__ import annotations

import base64

_PREFIX = "enc::"

try:  # Windows DPAPI via pywin32
    import win32crypt  # type: ignore

    _DPAPI = True
except Exception:  # pragma: no cover - non-Windows / missing pywin32
    _DPAPI = False


def encrypt(value: str) -> str:
    if not value or value.startswith(_PREFIX):
        return value
    raw = value.encode("utf-8")
    if _DPAPI:
        blob = win32crypt.CryptProtectData(raw, "aria2", None, None, None, 0)
        return _PREFIX + "dpapi:" + base64.b64encode(blob).decode("ascii")
    return _PREFIX + "b64:" + base64.b64encode(raw).decode("ascii")


def decrypt(value: str) -> str:
    if not value or not value.startswith(_PREFIX):
        return value
    body = value[len(_PREFIX) :]
    try:
        scheme, _, data = body.partition(":")
        blob = base64.b64decode(data)
        if scheme == "dpapi" and _DPAPI:
            _, raw = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
            return raw.decode("utf-8")
        if scheme == "b64":
            return blob.decode("utf-8")
    except Exception:
        return ""
    return ""


# Field names treated as secrets wherever settings are persisted.
SECRET_KEYS = {
    "claude_api_key",
    "openai_api_key",
    "voyage_api_key",
    "telegram_bot_token",
    "discord_webhook_url",
    "discord_bot_token",
    "openai_oauth_token",
    "openai_oauth_refresh",
    "grok_api_key",
    "grok_oauth_token",
    "grok_oauth_refresh",
    "gemini_api_key",
    "oai_compat_api_key",
}


def encrypt_settings(d: dict) -> dict:
    out = dict(d)
    for k in SECRET_KEYS:
        if isinstance(out.get(k), str):
            out[k] = encrypt(out[k])
    return out


def decrypt_settings(d: dict) -> dict:
    out = dict(d)
    for k in SECRET_KEYS:
        if isinstance(out.get(k), str):
            out[k] = decrypt(out[k])
    return out
