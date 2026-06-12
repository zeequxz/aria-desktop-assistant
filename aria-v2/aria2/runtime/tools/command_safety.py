"""runtime/tools/command_safety.py - Classify obviously-destructive commands.

`run_shell` / `run_python` execute with the user's full OS privileges (the
"sandbox" only pins the working dir). So a prompt-injected web page or file
could try `rm -rf ~`, `curl evil | sh`, `format`, etc. — and in an auto/accept
trust mode those would run with no human in the loop.

This flags such commands so the permission gate can FORCE an approval dialog even
when policy says "allow" (and, where there's no approver — Telegram, automations
— they're denied, which is the safe default). It's intentionally a coarse,
high-signal denylist: it never *blocks* a confirmed command, it just refuses to
run dangerous ones silently.
"""

from __future__ import annotations

import re

# (regex, human reason). Matched case-insensitively against the command text.
_DANGEROUS: list[tuple[str, str]] = [
    (r"\brm\s+-[a-z]*[rf]", "recursive/forced delete (rm -rf)"),
    (r"\brmdir\s+/s", "recursive directory delete (rmdir /s)"),
    (r"\b(del|erase)\b[^\n]*[\\/][^\n]*[*?]", "wildcard file delete"),
    (r"\bdel\s+/[a-z]*[sq]", "recursive/quiet delete (del /s)"),
    (r"\b(format|mkfs|diskpart|fdisk)\b", "disk format/partition"),
    (r">\s*(/dev/sd|/dev/disk|\\\\\.\\physicaldrive)", "raw disk write"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power control"),
    (r"\breg\s+delete\b", "registry delete"),
    (r"\brd\s+/s", "recursive directory delete (rd /s)"),
    (r"\b(curl|wget|iwr|invoke-webrequest)\b[^\n|]*\|\s*(sh|bash|zsh|python|perl|powershell|pwsh|cmd)",
     "download piped straight into a shell (remote code execution)"),
    (r"\bpowershell\b[^\n]*(-enc\b|-encodedcommand\b|-e\s)", "obfuscated PowerShell (-EncodedCommand)"),
    (r"\b(iex|invoke-expression)\b", "Invoke-Expression (arbitrary code)"),
    (r"\b(ncat|netcat)\b|\bnc\s+-[a-z]*e", "netcat reverse shell"),
    (r"\bchmod\s+-?R?\s*777\b", "world-writable chmod 777"),
    (r"\b(sudo|runas)\b", "privilege escalation"),
    (r"\bcipher\s+/w", "secure-wipe free space"),
    (r"\bvssadmin\b[^\n]*delete", "delete shadow copies (ransomware pattern)"),
    (r"\bgit\b[^\n]*\bpush\b[^\n]*(--force|-f\b)", "force push (history rewrite)"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in _DANGEROUS]


def is_dangerous(command: str) -> tuple[bool, str]:
    """Return (dangerous, reason). reason is "" when the command looks safe."""
    text = command or ""
    for rx, reason in _COMPILED:
        if rx.search(text):
            return True, reason
    return False, ""
