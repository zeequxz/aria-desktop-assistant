"""core/ids.py - Sortable, prefixed unique identifiers.

IDs are time-prefixed so a plain string sort is also a chronological sort,
which keeps timeline queries cheap without an extra index.
"""

import os
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def new_id(prefix: str = "") -> str:
    """Return a unique, lexicographically-sortable id, optionally prefixed.

    Format: <prefix>_<ms-since-epoch base36><random base36>
    """
    ts = _b36(int(time.time() * 1000))
    rand = _b36(int.from_bytes(os.urandom(5), "big"))
    core = f"{ts}{rand}"
    return f"{prefix}_{core}" if prefix else core


def now_ms() -> int:
    return int(time.time() * 1000)
