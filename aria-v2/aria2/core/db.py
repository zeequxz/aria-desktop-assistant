"""core/db.py - SQLite data layer (WAL, thread-safe, transactional).

One database file, one connection guarded by a re-entrant lock. SQLite with WAL
comfortably handles a single-process multithreaded desktop app (UI thread,
scheduler thread, run-engine threads). Every write goes through `tx()` so we
never get the half-written / last-write-wins corruption that the v1 JSON blob
was prone to.

Rows come back as dict-like sqlite3.Row. Helpers (`one`, `all`, `execute`,
`insert`, `update`) keep call sites terse.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from aria2.core import config
from aria2.core.ids import now_ms


def _load_schema() -> str:
    """Read schema.sql, working both from source and from a PyInstaller bundle.

    When frozen, bundled data lives under sys._MEIPASS; otherwise it sits next to
    this module. Try both so the same code path serves dev and packaged runs."""
    candidates = [Path(__file__).parent / "schema.sql"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, Path(meipass) / "aria2" / "core" / "schema.sql")
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError("schema.sql not found (dev or bundled).")


_SCHEMA = _load_schema()

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    config.app_dir()  # ensure dir exists
    c = sqlite3.connect(
        str(config.DB_FILE),
        check_same_thread=False,  # we serialise access ourselves via _lock
        timeout=30.0,
    )
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA synchronous = NORMAL")
    # Wait (don't immediately error) if another thread holds a write lock —
    # the engine now runs UI + scheduler + parallel delegation + messaging
    # concurrently against one connection.
    c.execute("PRAGMA busy_timeout = 5000")
    c.executescript(_SCHEMA)
    from aria2.core import migrations

    migrations.migrate(c)
    c.commit()
    _conn = c
    return c


def init() -> None:
    """Create the schema and seed built-in data. Idempotent."""
    with _lock:
        _connect()
    _seed()


@contextmanager
def tx():
    """Transaction context. Commits on success, rolls back on exception."""
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def execute(sql: str, params: tuple | dict = ()):  # noqa: ANN001
    with tx() as conn:
        return conn.execute(sql, params)


def one(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:  # noqa: ANN001
    with _lock:
        cur = _connect().execute(sql, params)
        return cur.fetchone()


def all(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:  # noqa: ANN001,A001
    with _lock:
        cur = _connect().execute(sql, params)
        return cur.fetchall()


def insert(table: str, row: dict) -> None:
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", row)


def update(table: str, id_value: str, changes: dict, id_col: str = "id") -> None:
    if not changes:
        return
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    params = dict(changes)
    params["_id"] = id_value
    execute(f"UPDATE {table} SET {sets} WHERE {id_col} = :_id", params)


def delete(table: str, id_value: str, id_col: str = "id") -> None:
    execute(f"DELETE FROM {table} WHERE {id_col} = ?", (id_value,))


# ── Seed data ────────────────────────────────────────────────────────────────

_BUILTIN_AGENTS = [
    (
        "assistant", "Assistant", "✦", "#6c8fff",
        "General-purpose helper for any task.",
        "You are ARIA, a capable local AI assistant running on the user's own "
        "machine. Be concise and direct. You have memory of the user and their "
        "projects, and tools for files, shell, and search. Always ask before "
        "destructive actions unless the user has clearly authorised them.",
    ),
    (
        "researcher", "Researcher", "◈", "#ffdd6c",
        "Finds, synthesises, and cites information.",
        "You are a research specialist. Gather information, synthesise it, and "
        "present clear findings with sources and key takeaways highlighted.",
    ),
    (
        "coder", "Coder", "⌘", "#6cffb8",
        "Reads, writes, and runs code in the project folder.",
        "You are a senior software engineer working in the user's project folder. "
        "Read before you write, match existing style, run code in the sandbox to "
        "verify, and explain changes succinctly.",
    ),
    (
        "writer", "Writer", "✍", "#ff8c6c",
        "Drafts and edits written content.",
        "You are an expert writer. Draft and refine emails, docs, and articles. "
        "Match the user's tone; ask about audience and purpose when unsure.",
    ),
]


def _seed() -> None:
    ts = now_ms()
    with tx() as conn:
        # Default project
        exists = conn.execute(
            "SELECT 1 FROM projects WHERE id = 'general'"
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO projects (id,name,folder,created_at,updated_at) "
                "VALUES ('general','General','',?,?)",
                (ts, ts),
            )
        # Built-in agents
        for aid, name, icon, color, desc, system in _BUILTIN_AGENTS:
            has = conn.execute("SELECT 1 FROM agents WHERE id = ?", (aid,)).fetchone()
            if not has:
                conn.execute(
                    "INSERT INTO agents (id,name,icon,color,description,"
                    "system_prompt,memory_scope,builtin,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,'project',1,?,?)",
                    (aid, name, icon, color, desc, system, ts, ts),
                )
