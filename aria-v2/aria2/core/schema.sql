-- aria-v2 schema. Entities are rows, not JSON blobs.
-- Embeddings are stored as BLOB (packed float32) and scored in Python so we
-- carry no native vector-extension dependency; FTS5 (built into SQLite) backs
-- keyword search. This is plenty fast for a single-user desktop corpus.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- ── Organisation ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  folder        TEXT DEFAULT '',
  goals         TEXT DEFAULT '',
  settings_json TEXT DEFAULT '{}',
  archived      INTEGER DEFAULT 0,
  pinned        INTEGER DEFAULT 0,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
  id                      TEXT PRIMARY KEY,
  project_id              TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title                   TEXT DEFAULT 'New chat',
  agent_id                TEXT,
  parent_chat_id          TEXT,             -- fork lineage
  branch_point_message_id TEXT,             -- message we forked from
  pinned                  INTEGER DEFAULT 0,
  archived                INTEGER DEFAULT 0,
  created_at              INTEGER NOT NULL,
  updated_at              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  id           TEXT PRIMARY KEY,
  chat_id      TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  parent_id    TEXT,                          -- message tree (branching)
  role         TEXT NOT NULL,                 -- user | assistant | tool | system
  content_json TEXT NOT NULL,                 -- list of content blocks
  model        TEXT,
  token_in     INTEGER DEFAULT 0,
  token_out    INTEGER DEFAULT 0,
  cost_usd     REAL DEFAULT 0,
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, created_at);

-- ── Agents (replaces v1's hardcoded list) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  icon            TEXT DEFAULT '✦',
  color           TEXT DEFAULT '#6c8fff',
  description     TEXT DEFAULT '',
  system_prompt   TEXT DEFAULT '',
  provider        TEXT,                        -- null = use global default
  model           TEXT,
  tool_scopes_json TEXT DEFAULT '{}',          -- {tool_name: allow|ask|deny}
  memory_scope    TEXT DEFAULT 'project',      -- user|project|agent|none
  builtin         INTEGER DEFAULT 0,
  parent_agent_id TEXT,
  version         INTEGER DEFAULT 1,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

-- ── Durable runs ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,                 -- chat|task|delegated|trigger
  status        TEXT NOT NULL,                 -- queued|running|paused|done|failed|cancelled
  agent_id      TEXT,
  project_id    TEXT,
  chat_id       TEXT,
  parent_run_id TEXT,                          -- delegation tree
  trigger_id    TEXT,
  title         TEXT DEFAULT '',
  budget_usd    REAL DEFAULT 0,
  cost_usd      REAL DEFAULT 0,
  token_total   INTEGER DEFAULT 0,
  error         TEXT,
  forked_from_run_id TEXT,                     -- replay: lineage of a fork
  forked_from_step   INTEGER,                  -- replay: step we branched at
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS run_steps (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  idx         INTEGER NOT NULL,
  type        TEXT NOT NULL,                   -- model|tool|plan|handoff|error
  tool_name   TEXT,
  input_json  TEXT,
  output_json TEXT,
  messages_json TEXT,                          -- replay: context snapshot at this step
  token_in    INTEGER DEFAULT 0,
  token_out   INTEGER DEFAULT 0,
  duration_ms INTEGER DEFAULT 0,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON run_steps(run_id, idx);

-- ── Automation ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS triggers (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL,                   -- schedule|file|webhook|manual
  config_json TEXT DEFAULT '{}',
  project_id  TEXT,
  agent_id    TEXT,
  prompt      TEXT DEFAULT '',
  enabled     INTEGER DEFAULT 1,
  max_retries INTEGER DEFAULT 0,
  last_fired  INTEGER,
  next_run    INTEGER,
  last_run_id TEXT,
  created_at  INTEGER NOT NULL
);

-- ── Memory (semantic + episodic, scored, decaying) ──────────────────────────
CREATE TABLE IF NOT EXISTS memories (
  id            TEXT PRIMARY KEY,
  scope         TEXT NOT NULL,                 -- user|project|agent
  scope_id      TEXT DEFAULT '',
  kind          TEXT DEFAULT 'semantic',       -- semantic|episodic|preference
  text          TEXT NOT NULL,
  embedding     BLOB,                          -- packed float32
  importance    REAL DEFAULT 0.5,
  confidence    REAL DEFAULT 0.7,              -- provenance: belief strength
  access_count  INTEGER DEFAULT 0,
  source_run_id TEXT,                          -- provenance: run that produced it
  derived_from  TEXT DEFAULT '[]',            -- provenance: parent memory ids (JSON)
  retracted     INTEGER DEFAULT 0,            -- provenance: belief revision
  superseded_by TEXT,                          -- provenance: replacement memory id
  needs_review  INTEGER DEFAULT 0,            -- flagged when a dependency retracts
  pinned        INTEGER DEFAULT 0,
  created_at    INTEGER NOT NULL,
  last_accessed INTEGER,
  expires_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope, scope_id);

-- ── Ambient capture: observations + mined automation proposals ───────────────
CREATE TABLE IF NOT EXISTS observations (
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,                    -- file_change|clipboard|command|app
  project_id TEXT,
  signature  TEXT,                             -- normalised key for pattern mining
  data_json  TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_sig ON observations(signature, created_at);

CREATE TABLE IF NOT EXISTS proposals (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,                   -- automation|memory|agent
  title       TEXT NOT NULL,
  rationale   TEXT,
  payload_json TEXT,                           -- enough to materialise on accept
  status      TEXT DEFAULT 'pending',          -- pending|accepted|dismissed
  confidence  REAL DEFAULT 0.5,
  created_at  INTEGER NOT NULL
);

-- ── Knowledge / RAG ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
  id           TEXT PRIMARY KEY,
  project_id   TEXT,
  uri          TEXT,
  title        TEXT,
  content_hash TEXT,
  version      INTEGER DEFAULT 1,
  ingested_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id            TEXT PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal       INTEGER NOT NULL,
  text          TEXT NOT NULL,
  embedding     BLOB,
  metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id, ordinal);

-- ── Learned routing: per-agent, per-task-type performance ────────────────────
CREATE TABLE IF NOT EXISTS agent_skill_stats (
  agent_id   TEXT NOT NULL,
  task_type  TEXT NOT NULL,
  runs       INTEGER DEFAULT 0,
  successes  INTEGER DEFAULT 0,
  total_cost REAL DEFAULT 0,
  total_ms   INTEGER DEFAULT 0,
  updated_at INTEGER,
  PRIMARY KEY (agent_id, task_type)
);

-- ── MCP connectors: external tool servers ────────────────────────────────────
CREATE TABLE IF NOT EXISTS connectors (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  transport  TEXT DEFAULT 'stdio',         -- stdio | http
  command    TEXT,
  args_json  TEXT DEFAULT '[]',
  env_json   TEXT DEFAULT '{}',
  url        TEXT,
  auth_json  TEXT DEFAULT '{}',              -- none | bearer | oauth (+ tokens)
  enabled    INTEGER DEFAULT 1,
  created_at INTEGER NOT NULL
);

-- ── Governance ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
  id         TEXT PRIMARY KEY,
  actor      TEXT,
  action     TEXT NOT NULL,
  target     TEXT,
  detail_json TEXT,
  run_id     TEXT,
  created_at INTEGER NOT NULL
);
