"""SQLite DDL for all Clockify CLI tables."""

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

WORKSPACES_DDL = """
CREATE TABLE IF NOT EXISTS workspaces (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    currency_code   TEXT,
    image_url       TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CLIENTS_DDL = """
CREATE TABLE IF NOT EXISTS clients (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    name            TEXT NOT NULL,
    archived        INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
)
"""

PROJECTS_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    client_id       TEXT,
    name            TEXT NOT NULL,
    color           TEXT,
    archived        INTEGER NOT NULL DEFAULT 0,
    billable        INTEGER NOT NULL DEFAULT 0,
    public          INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
    FOREIGN KEY (client_id) REFERENCES clients(id)
)
"""

USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    name            TEXT NOT NULL,
    email           TEXT,
    status          TEXT,
    role            TEXT,
    avatar_url      TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
)
"""

TIME_ENTRIES_DDL = """
CREATE TABLE IF NOT EXISTS time_entries (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    project_id      TEXT,
    description     TEXT,
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    duration        INTEGER,
    billable        INTEGER NOT NULL DEFAULT 0,
    is_locked       INTEGER NOT NULL DEFAULT 0,
    task_id         TEXT,
    tag_ids         TEXT,
    approval_status TEXT NOT NULL DEFAULT 'NOT_SUBMITTED',
    approver_id     TEXT,
    approver_name   TEXT,
    approved_at     TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
)
"""

TIME_ENTRIES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_te_user    ON time_entries(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_te_project ON time_entries(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_te_start   ON time_entries(start_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_te_ws      ON time_entries(workspace_id)",
]

SYNC_LOG_DDL = """
CREATE TABLE IF NOT EXISTS sync_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id        TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    status              TEXT NOT NULL,
    started_at          TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at        TEXT,
    records_fetched     INTEGER DEFAULT 0,
    records_upserted    INTEGER DEFAULT 0,
    last_entry_time     TEXT,
    error_message       TEXT,
    UNIQUE(workspace_id, entity_type)
)
"""

FIBERY_PUSH_LOG_DDL = """
CREATE TABLE IF NOT EXISTS fibery_push_log (
    workspace_id        TEXT PRIMARY KEY,
    last_pushed_at      TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
)
"""

# Ordered list of all statements to run at startup
ALL_DDL: list[str] = [
    SCHEMA_VERSION_DDL,
    WORKSPACES_DDL,
    CLIENTS_DDL,
    PROJECTS_DDL,
    USERS_DDL,
    TIME_ENTRIES_DDL,
    *TIME_ENTRIES_INDEXES,
    SYNC_LOG_DDL,
    FIBERY_PUSH_LOG_DDL,
]

CURRENT_SCHEMA_VERSION = 4
