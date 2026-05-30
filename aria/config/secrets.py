"""
config/secrets.py - Encrypt sensitive settings at rest using Windows DPAPI.

Secrets (API keys, Telegram bot token, Discord webhook URLs) are encrypted with
the current Windows user's key via DPAPI (CryptProtectData). That means a copy
of settings.json is useless on another machine or under another Windows account
- the OS refuses to decrypt it - so simply grabbing the file does not expose the
keys.

No master password is involved; protection is tied to the Windows login. If
pywin32 isn't available (e.g. non-Windows), values are stored as plain text so
the app still works, just without the at-rest protection.

Encrypted values are stored as the string "enc:v1:<base64>". Plain values (e.g.
from an older settings file) are returned unchanged by unprotect(), so existing
files migrate transparently the next time settings are saved.
"""

import base64

try:
    import win32crypt  # from pywin32

    _DPAPI = True
except Exception:  # pragma: no cover - non-Windows / missing pywin32
    _DPAPI = False

_PREFIX = "enc:v1:"

# Top-level setting keys whose string value should be encrypted.
SECRET_KEYS = [
    "claude_api_key",
    "openai_api_key",
    "telegram_bot_token",
    "discord_webhook_url",
]


def available() -> bool:
    """True if real (DPAPI) encryption is available on this machine."""
    return _DPAPI


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def protect(plaintext: str) -> str:
    """Encrypt a string for storage. Returns 'enc:v1:<base64>', or the original
    text if it's empty or DPAPI isn't available."""
    if not plaintext or not _DPAPI:
        return plaintext
    if is_encrypted(plaintext):
        return plaintext  # already encrypted
    try:
        blob = win32crypt.CryptProtectData(
            plaintext.encode("utf-8"), "ARIA", None, None, None, 0
        )
        return _PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception:
        return plaintext


def unprotect(value: str) -> str:
    """Decrypt a stored value. Plain (unencrypted) strings are returned as-is."""
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    if not _DPAPI:
        # Can't decrypt here; hand back empty rather than the ciphertext so the
        # app doesn't try to use a blob as an API key.
        return ""
    try:
        blob = base64.b64decode(value[len(_PREFIX) :])
        _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode("utf-8")
    except Exception:
        return ""


def encrypt_settings(s: dict) -> dict:
    """Return a copy of the settings dict with secret fields encrypted, ready to
    write to disk. Does not mutate the input."""
    out = dict(s)
    for key in SECRET_KEYS:
        if key in out and isinstance(out[key], str):
            out[key] = protect(out[key])
    # Discord channel webhook URLs are secrets too.
    chans = out.get("discord_channels")
    if isinstance(chans, list):
        out["discord_channels"] = [
            (
                {**c, "url": protect(c["url"])}
                if isinstance(c, dict) and c.get("url")
                else c
            )
            for c in chans
        ]
    return out


def decrypt_settings(s: dict) -> dict:
    """Return a copy of the settings dict with secret fields decrypted to plain
    text for in-app use. Does not mutate the input."""
    out = dict(s)
    for key in SECRET_KEYS:
        if key in out and isinstance(out[key], str):
            out[key] = unprotect(out[key])
    chans = out.get("discord_channels")
    if isinstance(chans, list):
        out["discord_channels"] = [
            (
                {**c, "url": unprotect(c["url"])}
                if isinstance(c, dict) and c.get("url")
                else c
            )
            for c in chans
        ]
    return out
