"""core/migrations.py - Idempotent, additive schema migrations.

schema.sql uses CREATE TABLE IF NOT EXISTS, which never alters an existing table.
This module brings older databases up to date by adding missing columns/tables.
Every step checks current state first, so running it repeatedly is safe.
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    if col not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def migrate(conn: sqlite3.Connection) -> None:
    # Provenance memory.
    _add_column(conn, "memories", "confidence", "REAL DEFAULT 0.7")
    _add_column(conn, "memories", "derived_from", "TEXT DEFAULT '[]'")
    _add_column(conn, "memories", "retracted", "INTEGER DEFAULT 0")
    _add_column(conn, "memories", "superseded_by", "TEXT")
    _add_column(conn, "memories", "needs_review", "INTEGER DEFAULT 0")

    # Run replay.
    _add_column(conn, "runs", "forked_from_run_id", "TEXT")
    _add_column(conn, "runs", "forked_from_step", "INTEGER")
    _add_column(conn, "run_steps", "messages_json", "TEXT")

    # Indexes for the run-tree lookups delegation + the inspector hit.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_chat ON runs(chat_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id)")

    # Ambient capture tables (CREATE IF NOT EXISTS is safe to repeat).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT,
            signature TEXT, data_json TEXT, created_at INTEGER NOT NULL)"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_obs_sig ON observations(signature, created_at)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
            rationale TEXT, payload_json TEXT, status TEXT DEFAULT 'pending',
            confidence REAL DEFAULT 0.5, created_at INTEGER NOT NULL)"""
    )

    # Learned routing: per-agent, per-task-type performance (the self-improving org).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_skill_stats (
            agent_id   TEXT NOT NULL,
            task_type  TEXT NOT NULL,
            runs       INTEGER DEFAULT 0,
            successes  INTEGER DEFAULT 0,
            total_cost REAL DEFAULT 0,
            total_ms   INTEGER DEFAULT 0,
            updated_at INTEGER,
            PRIMARY KEY (agent_id, task_type))"""
    )

    # MCP connectors: external tool servers (stdio/http) in the same tool registry.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS connectors (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            transport  TEXT DEFAULT 'stdio',
            command    TEXT,
            args_json  TEXT DEFAULT '[]',
            env_json   TEXT DEFAULT '{}',
            url        TEXT,
            enabled    INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL)"""
    )
    # Auth for HTTP MCP connectors (none | bearer | oauth): tokens + flow config.
    _add_column(conn, "connectors", "auth_json", "TEXT DEFAULT '{}'")

    # Projects can be pinned (like chats).
    _add_column(conn, "projects", "pinned", "INTEGER DEFAULT 0")

    # Per-chat provider and execution mode overrides.
    _add_column(conn, "chats", "provider_key", "TEXT DEFAULT ''")
    _add_column(conn, "chats", "exec_mode",    "TEXT DEFAULT ''")
    _add_column(conn, "chats", "chat_mode",    "TEXT DEFAULT ''")

    # Project trust level (controls default tool permissions for all its chats).
    _add_column(conn, "projects", "trust_level", "TEXT DEFAULT 'ask'")

    # Prompt version history — every agent system-prompt revision is snapshotted
    # so it can be rolled back (and self-improvement edits are auditable).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_prompt_versions (
            id            TEXT PRIMARY KEY,
            agent_id      TEXT NOT NULL,
            version       INTEGER NOT NULL,
            system_prompt TEXT NOT NULL,
            note          TEXT,
            created_at    INTEGER NOT NULL)"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_prompt_versions_agent "
        "ON agent_prompt_versions(agent_id, version)"
    )
