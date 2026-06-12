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

# WAL lets many threads read concurrently with a single writer. We give each
# thread its own connection (so reads never block each other) and serialise only
# WRITES with a process-wide lock (so we never thrash on SQLITE_BUSY). This lifts
# the old "one connection behind one RLock" bottleneck for the read-heavy path.
_write_lock = threading.Lock()
_local = threading.local()


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is not None:
        return c
    config.app_dir()  # ensure dir exists
    c = sqlite3.connect(
        str(config.DB_FILE),
        check_same_thread=False,  # each thread has its own connection
        timeout=30.0,
    )
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA synchronous = NORMAL")
    c.execute("PRAGMA busy_timeout = 5000")
    _local.conn = c
    return c


def _reset() -> None:
    """Drop this thread's connection (used by tests that re-point DB_FILE)."""
    c = getattr(_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _local.__dict__.pop("conn", None)


def init() -> None:
    """Create the schema, seed built-in data, recover orphaned runs. Idempotent."""
    conn = _conn()
    conn.executescript(_SCHEMA)
    from aria2.core import migrations

    migrations.migrate(conn)
    conn.commit()
    _seed()
    _reconcile_interrupted_runs()


def _reconcile_interrupted_runs() -> None:
    """Any run still marked 'running' at startup is orphaned from a previous
    crash/kill — mark it 'interrupted' so it can't linger forever (and so the
    Runs view + reliability metrics tell the truth)."""
    try:
        with tx() as conn:
            conn.execute(
                "UPDATE runs SET status='interrupted', ended_at=? "
                "WHERE status='running'", (now_ms(),))
    except Exception:
        pass


@contextmanager
def tx():
    """Write transaction. Serialised across threads; commit on success, rollback
    on exception."""
    conn = _conn()
    with _write_lock:
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
    # Lock-free read (WAL MVCC snapshot on this thread's own connection).
    return _conn().execute(sql, params).fetchone()


def all(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:  # noqa: ANN001,A001
    return _conn().execute(sql, params).fetchall()


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
    (
        "reviewer", "Reviewer", "⚖", "#c89bff",
        "Critiques work for correctness, bugs, and security.",
        "You are a senior code reviewer. Judge work for correctness, bugs, "
        "security, and missed requirements. Start your reply with APPROVE (only if "
        "it is genuinely solid) or REVISE (if anything needs changing), then give "
        "specific, actionable points — no praise padding.",
    ),
    (
        "tester", "Tester", "🧪", "#6cd0ff",
        "Designs and runs tests; verifies behaviour.",
        "You are a QA test engineer. Derive concrete test cases (including edge "
        "cases) from the requirements, run them where possible, and report exactly "
        "what passes and what fails with evidence. Do not assume — verify.",
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
