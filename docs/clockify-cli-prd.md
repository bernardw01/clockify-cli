Product Requirements Document
Clockify CLI Sync Tool
Version 2.0 — 2026-03-23

════════════════════════════════════════════════════════════════════════════════
1. OVERVIEW
════════════════════════════════════════════════════════════════════════════════

1.1  Purpose
────────────
clockify-cli is a local-first command-line tool that mirrors a Clockify.me
account into a SQLite database on disk.  It presents a full terminal user
interface (TUI) so users can configure credentials, run data syncs with live
progress feedback, and browse time entries — all without leaving the terminal.

1.2  Goals
──────────
• Download all time entries, projects, users, and clients for a Clockify
  workspace and store them locally for offline access and downstream querying.
• Provide a polished, interactive TUI (htop-style) rather than a plain CLI.
• Support both incremental (new-records-only) and full (all-records) sync modes.
• Emit comprehensive, structured logs of every API call and DB operation so
  issues can be diagnosed without re-running the tool.
• Be installable from source via uv on macOS (including iCloud Drive paths).

1.3  Out of Scope (v2.0)
────────────────────────
• Write-back to the Clockify API (the tool is read-only with respect to Clockify).
• Multi-workspace sync in a single run.
• Tasks, tags, or expense syncing.
• A web or desktop GUI.
• Windows / Linux packaging.
• Fibery bill rate / cost rate field population (left blank; computed by Fibery formulas).


════════════════════════════════════════════════════════════════════════════════
2. TARGET USERS
════════════════════════════════════════════════════════════════════════════════

Primary   — Individuals or small teams who use Clockify for time tracking and
            want a local copy of their data for custom reporting, backup, or
            offline analysis.

Secondary — Developers or power users who want to query time data in SQL
            without relying on Clockify's web reports.


════════════════════════════════════════════════════════════════════════════════
3. FUNCTIONAL REQUIREMENTS
════════════════════════════════════════════════════════════════════════════════

3.1  Configuration
──────────────────
FR-01  The tool MUST store configuration in ~/.config/clockify-cli/config.json
       with file permissions 0600.
FR-02  The config MUST hold at minimum: api_key (str), workspace_id (str),
       last_sync (ISO-8601 timestamp, nullable).
FR-03  The API key MUST be readable from the CLOCKIFY_API_KEY environment
       variable, which takes priority over the stored config value.
FR-04  A Settings screen MUST allow the user to enter the API key and select
       their workspace from a live-fetched dropdown.

3.2  Data Sync
──────────────
FR-05  The sync pipeline MUST fetch and upsert the following entity types in
       dependency order: workspace → clients → projects → users → time entries.
FR-06  The tool MUST support a FULL sync mode that fetches all records from
       Clockify regardless of what is already stored locally.
FR-07  The tool MUST support an INCREMENTAL sync mode that uses the latest
       stored start_time per user as the API `start` parameter, fetching only
       new entries.
FR-08  All entities MUST be written to SQLite using INSERT OR REPLACE (upsert)
       semantics so re-running is idempotent.
FR-09  The sync MUST handle multi-user workspaces by iterating over every
       workspace user and fetching their time entries independently.
FR-10  The sync MUST tolerate paginated API responses, consuming every page
       before moving to the next entity.
FR-11  The sync MUST store a row in sync_log for each entity after every run,
       recording status, record counts, and the most recent entry timestamp.
FR-12  If an entity sync fails (API error, FK violation, etc.) the error MUST
       be captured in sync_log and displayed in the TUI; other entities MUST
       continue unless they depend on the failed one.

3.3  Time Entries
─────────────────
FR-13  Every time entry stored MUST include: id, workspace_id, user_id,
       project_id (nullable), description, start_time, end_time (nullable for
       running timers), duration_seconds (nullable), billable flag, is_locked
       flag, task_id (nullable), tag_ids (JSON array stored as TEXT).
FR-14  The tool MUST handle the Clockify API returning tagIds: null by
       coercing null to an empty list before validation.
FR-15  Indexes MUST exist on time_entries(user_id), (project_id),
       (start_time DESC), and (workspace_id) to support fast queries.

3.4  API Client
───────────────
FR-16  All HTTP calls MUST use an asyncio.Semaphore(10) to cap concurrent
       requests and avoid exceeding Clockify's 50 req/sec limit.
FR-17  The client MUST map HTTP status codes to typed exceptions:
         401/403 → AuthError
         404     → NotFoundError
         429     → RateLimitError
         5xx     → ServerError
         other   → ClockifyAPIError
FR-18  The total page count for time entries MUST be read from the
       X-Total-Count response header to allow accurate progress reporting.

3.5  TUI Screens
────────────────
FR-19  On launch, the TUI MUST detect whether credentials are configured:
       • Unconfigured → open Settings screen.
       • Configured   → open Main Menu screen.
FR-20  Main Menu MUST offer navigation to: Sync Data, View Time Entries,
       Push to Fibery, Settings, and Quit.
FR-21  Sync Screen MUST display, for each entity (clients, projects, users,
       time entries):
       • A labelled progress bar showing percentage complete.
       • A record counter (upserted / fetched).
       • A status chip (waiting / syncing / done / error) with distinct colours.
FR-22  Progress percentage for time entries MUST reach 100 % on completion by
       tracking cumulative pages across all users (not per-user page counts).
FR-23  Sync mode MUST be toggleable between Incremental and Full via both:
       • The "Mode: [mode] [i]" button (primary — always clickable).
       • The keyboard shortcut i (secondary).
       The button label MUST update to reflect the current mode immediately.
FR-24  A scrollable log panel on the Sync Screen MUST surface timestamped
       messages: sync start/complete, per-entity errors, and the active mode.
FR-25  Time Entries screen MUST display entries in a table with columns:
       date, project, description, duration, billable flag.
FR-26  Time Entries screen MUST support debounced (300 ms) full-text search
       across description, project name, and user name.
FR-27  All screens MUST dismiss back to their parent via Escape key.

3.6  Logging
────────────
FR-28  The tool MUST emit structured logs to
       ~/.local/share/clockify-cli/logs/clockify-cli.log via loguru.
FR-29  Every outbound HTTP request MUST be logged at DEBUG level with:
       method, URL, query parameters.
FR-30  Every HTTP response MUST be logged with: status code, elapsed ms,
       response size in bytes, and up to 500 characters of body preview.
       Responses with status >= 400 MUST log at WARNING level.
FR-31  The API key MUST be masked in all log output (last 4 characters only).
FR-32  Pagination fetches MUST log each page number and item count.
FR-33  Log files MUST rotate at 10 MB and retain the last 5 files.


════════════════════════════════════════════════════════════════════════════════
4. NON-FUNCTIONAL REQUIREMENTS
════════════════════════════════════════════════════════════════════════════════

NFR-01  Performance
       The sync of a workspace with ~1 000 time entries across 2 users MUST
       complete in under 30 seconds on a standard broadband connection.

NFR-02  Reliability / Idempotency
       Running the sync multiple times MUST NOT create duplicate rows.  A
       full sync followed by an incremental sync MUST NOT change DB row counts
       when no new entries exist in Clockify.

NFR-03  Correctness — Foreign Keys
       SQLite PRAGMA foreign_keys=ON MUST be set on every connection.  The
       workspace row MUST be upserted before any entity that references it.
       If the workspace is not returned by the API, a placeholder row MUST be
       inserted so FK constraints do not fail.

NFR-04  Concurrency Safety
       The SQLite database MUST be opened in WAL (Write-Ahead Logging) mode
       to allow concurrent readers during writes.

NFR-05  Portability
       The tool MUST run on macOS 13+.  It MUST work when installed on an
       iCloud Drive path (spaces in path, tilde expansion) using a non-editable
       uv install.

NFR-06  Test Coverage
       The codebase MUST maintain a passing test suite of ≥ 90 tests covering:
       config, DB (database + 6 repositories), API client (models + HTTP),
       sync orchestrator, Fibery client, and push orchestrator.  No real API
       calls in tests; all HTTP is mocked via httpx.MockTransport.


════════════════════════════════════════════════════════════════════════════════
5. DATA MODEL
════════════════════════════════════════════════════════════════════════════════

Table               Primary Key   Foreign Keys                  Notes
────────────────    ───────────   ─────────────────────────     ──────────────────────
schema_version      version       —                             Migration tracking
workspaces          id            —                             Source of truth for FK
clients             id            workspace_id → workspaces     Includes archived rows
projects            id            workspace_id, client_id       Includes archived rows
users               id            workspace_id                  Workspace members
time_entries        id            workspace_id, user_id,        tag_ids stored as JSON
                                  project_id (nullable)         text; duration in secs
sync_log            id (auto)     —                             UNIQUE(workspace_id,
                                                                entity_type) for upsert

Schema version: 1  (increment on any breaking DDL change)

Note: No schema changes are required for v2.0.  The Fibery push reads from the
existing time_entries, users, and projects tables without modification.


════════════════════════════════════════════════════════════════════════════════
6. SYNC PIPELINE — SEQUENCE
════════════════════════════════════════════════════════════════════════════════

  ┌────────────────────────────────────────────────────────────────────────┐
  │  SyncOrchestrator.sync_all(workspace_id, incremental)                  │
  │                                                                        │
  │  1. _ensure_workspace()                                                │
  │     └─ GET /workspaces  →  upsert matching row  (or placeholder)       │
  │                                                                        │
  │  2. _sync_clients()                                                    │
  │     └─ GET /workspaces/{id}/clients  (all pages)  →  upsert            │
  │                                                                        │
  │  3. _sync_projects()                                                   │
  │     └─ GET /workspaces/{id}/projects  (all pages)  →  upsert           │
  │                                                                        │
  │  4. _sync_users()                                                      │
  │     └─ GET /workspaces/{id}/users  (all pages)  →  upsert              │
  │                                                                        │
  │  5. _sync_time_entries()    ← incremental flag applied here            │
  │     for each user in DB:                                               │
  │       start = get_latest_entry_time()  (if incremental)                │
  │       for each page of GET /workspaces/{id}/user/{uid}/time-entries:   │
  │         upsert page  →  notify TUI callback  →  yield event loop       │
  │                                                                        │
  │  6. Write sync_log  →  save config.last_sync                           │
  └────────────────────────────────────────────────────────────────────────┘

Progress tracking for time entries uses two cumulative counters:
  pages_done     — increments after every page, across all users
  pages_estimate — grows by total_pages on the first page for each user
  percent        = pages_done / pages_estimate × 100
This guarantees the progress bar reaches exactly 100 % when all users finish.


════════════════════════════════════════════════════════════════════════════════
7. API REFERENCE SUMMARY
════════════════════════════════════════════════════════════════════════════════

Endpoint                                                Method  Auth
──────────────────────────────────────────────────────  ──────  ────────────
/workspaces                                             GET     X-Api-Key
/workspaces/{id}/clients                                GET     X-Api-Key
/workspaces/{id}/projects                               GET     X-Api-Key
/workspaces/{id}/users                                  GET     X-Api-Key
/workspaces/{id}/user/{uid}/time-entries                GET     X-Api-Key

Pagination params : page (1-based), page-size (default 50, max 5000)
Total count header: X-Total-Count  (used to compute total_pages)
Rate limit        : 50 requests / second  (client caps at 10 concurrent)
Base URL          : https://api.clockify.me/api/v1


════════════════════════════════════════════════════════════════════════════════
8. TECHNICAL STACK
════════════════════════════════════════════════════════════════════════════════

Layer           Library / Tool     Version    Notes
────────────    ──────────────     ────────   ──────────────────────────────
Runtime         Python             3.12       via uv
TUI             Textual            ≥ 0.86     Screen/dismiss API (v8.x)
HTTP            httpx              ≥ 0.27     Async client + MockTransport
Validation      pydantic           ≥ 2.7      camelCase aliases, validators
Database        aiosqlite          ≥ 0.20     WAL mode, FK enforcement
Logging         loguru             ≥ 0.7      Rotating file handler
Packaging       uv + hatchling     —          Non-editable install required
Testing         pytest             ≥ 8.2      asyncio_mode = "auto"
                pytest-asyncio     ≥ 0.23
Linting         ruff + mypy        —

Install path: ~/.local/share/clockify-cli/  (DB + logs)
Config path : ~/.config/clockify-cli/config.json


════════════════════════════════════════════════════════════════════════════════
9. FILE STRUCTURE
════════════════════════════════════════════════════════════════════════════════

clockify-cli/
├── clockify_cli/
│   ├── main.py              Entry point — loguru setup, launch ClockifyApp
│   ├── config.py            Config dataclass, load/save, env var override
│   ├── constants.py         BASE_URL, paths, rate limit constant
│   ├── api/
│   │   ├── client.py        ClockifyClient — async HTTP, rate limiting, logging
│   │   ├── models.py        Pydantic v2 models (Workspace, Client, Project,
│   │   │                    WorkspaceUser, TimeEntry, TimeInterval)
│   │   └── exceptions.py    Typed exception hierarchy
│   ├── db/
│   │   ├── database.py      Database class — WAL, FK pragma, execute helpers
│   │   ├── schema.py        DDL for all 7 tables + indexes
│   │   └── repositories/
│   │       ├── workspaces.py
│   │       ├── clients.py
│   │       ├── projects.py
│   │       ├── users.py
│   │       ├── time_entries.py   upsert_many, get_latest_entry_time, search
│   │       └── sync_log.py       start/complete/fail_sync, get_last_sync
│   ├── sync/
│   │   ├── orchestrator.py  SyncOrchestrator — coordinates all entity syncs
│   │   └── progress.py      EntityProgress, SyncProgress dataclasses
│   └── tui/
│       ├── app.py           ClockifyApp — DB lifecycle, screen routing
│       ├── styles.tcss      Textual CSS for all screens
│       └── screens/
│           ├── main_menu.py
│           ├── settings.py
│           ├── sync_screen.py
│           ├── time_entries.py
│           └── fibery_push_screen.py
│   └── fibery/
│       ├── __init__.py
│       ├── client.py            FiberyClient — rate-limited API commands
│       ├── models.py            LaborCostPayload, PushProgress dataclasses
│       └── push_orchestrator.py FiberyPushOrchestrator — full reconciliation
├── tests/
│   ├── test_config.py           7 tests
│   ├── api/
│   │   └── test_client.py       16 tests  (httpx.MockTransport)
│   ├── db/
│   │   ├── test_database.py      6 tests
│   │   └── test_repositories.py 21 tests
│   ├── sync/
│   │   └── test_orchestrator.py 11 tests  (MagicMock)
│   └── fibery/
│       ├── test_client.py       17 tests  (httpx.MockTransport)
│       └── test_push_orchestrator.py  11 tests  (real DB + MagicMock client)
├── docs/
│   └── clockify-cli-prd.md  ← this document
├── pyproject.toml
├── Makefile
└── README.md


════════════════════════════════════════════════════════════════════════════════
10. KNOWN CONSTRAINTS AND WORKAROUNDS
════════════════════════════════════════════════════════════════════════════════

C-01  iCloud Drive install path
      Python's site module does not process .pth files on paths containing
      spaces (common on iCloud Drive).  A non-editable install
      (`uv pip install .`) is required so package files are physically copied
      into site-packages.  `make reinstall` automates this after code changes.

C-02  Textual 8.x breaking changes
      • pop_screen() removed — replaced by dismiss().
      • Log widget no longer accepts markup=True constructor argument.
      • Reactive watchers use identity comparison; mutating an object in-place
        does not re-fire the watcher.  The sync screen therefore updates TUI
        widgets directly in the progress callback rather than via a reactive.

C-03  Clockify API inconsistencies
      • tagIds may be returned as null instead of [].  A Pydantic
        field_validator coerces null → [] before model validation.
      • userId may be omitted from time entry response bodies when fetched via
        the per-user endpoint.  The orchestrator injects it from the loop
        variable before upserting.
      • Archived projects/clients are referenced by time entries.  The client
        fetches ALL projects and clients (no archived=false filter) to avoid
        FK violations on time_entries.project_id.

C-04  Workspace FK bootstrapping
      The workspace row must exist before any FK-dependent insert.  The
      orchestrator calls _ensure_workspace() at the start of every sync run;
      if the workspace is not returned by the API a placeholder row is inserted.


════════════════════════════════════════════════════════════════════════════════
11. FUTURE ENHANCEMENTS (POST v2.0)
════════════════════════════════════════════════════════════════════════════════

F-01  Multi-workspace support — select and sync multiple workspaces, storing
      all data in the same DB with workspace_id as partition key.
F-02  Task and tag sync — extend schema and orchestrator to capture tasks and
      tags so time entries can be fully denormalised for reporting.
F-03  Scheduled / background sync — run the sync on a cron-like schedule while
      the TUI is open, displaying a "last synced" timestamp.
F-04  Export — write filtered time entries to CSV or JSON from the TUI.
F-05  Reporting screen — summary charts (hours per project, per user, per week)
      rendered with Textual's built-in sparklines and DataTable.
F-06  Rate-limit retry — detect 429 responses and apply exponential back-off
      before retrying, rather than raising RateLimitError immediately.
F-07  Linux / Windows support — validate install on Ubuntu and Windows 11.
F-08  GitHub Actions CI — run the test suite on every push to main.


════════════════════════════════════════════════════════════════════════════════
12. ACCEPTANCE CRITERIA (v2.0 DEFINITION OF DONE)
════════════════════════════════════════════════════════════════════════════════

AC-01  `make reinstall && uv run clockify-cli` launches the TUI without errors.
AC-02  Entering a valid API key and selecting a workspace in Settings persists
       to config.json and the app proceeds to Main Menu on next launch.
AC-03  Full sync completes without error for a workspace containing clients,
       projects, users, and time entries (including archived projects).
AC-04  The Time Entries progress bar reaches 100 % at the end of sync
       regardless of how many users are in the workspace.
AC-05  Switching mode to "Full" via the Mode button causes the sync to fetch
       all time entries from the API (observable via log output showing
       "Starting full sync…" and API calls without a `start` parameter).
AC-06  Running incremental sync a second time with no new Clockify entries
       results in 0 records fetched per user (observable in the count column).
AC-07  The log file at ~/.local/share/clockify-cli/logs/clockify-cli.log
       contains request/response entries for every API call made.
AC-08  `uv run pytest tests/ -v` reports all tests passing (≥ 91 tests).
AC-09  Time entries with tagIds: null in the API response are stored
       successfully with tag_ids = "[]" in the DB.
AC-10  Entering a valid Fibery API key and pressing "Verify Fibery Connection"
       in Settings shows a "Connected ✓" confirmation without error.
AC-11  The Main Menu displays a "Push to Fibery" button and pressing it (or
       pressing F) opens the Fibery Push screen.
AC-12  Pressing "Start Push" on the Fibery Push screen runs the pre-flight
       lookups, shows the "Labor Costs" progress bar advancing, and completes
       with status "done" and pushed > 0 for a workspace with synced entries.
AC-13  After a successful push, entries are visible in harpin-ai.fibery.io →
       Agreement Management → Labor Costs with correct Time Log ID, hours,
       dates, user names, project names, and billable flags.
AC-14  Entries whose project_id matches a Fibery Agreement are linked to that
       Agreement; entries without a match have the Agreement field left blank.
AC-15  Running the push a second time with no changed entries completes without
       error and results in 0 new Fibery entities created (idempotent upsert
       via Time Log ID conflict field).


════════════════════════════════════════════════════════════════════════════════
13. FIBERY INTEGRATION (v2.0)
════════════════════════════════════════════════════════════════════════════════

13.1  Overview
──────────────
v2.0 adds a "Push to Fibery" capability that reads time entry data from the
local SQLite database and upserts it into the Agreement Management/Labor Costs
database in the Fibery workspace at harpin-ai.fibery.io.  No schema changes to
the local DB are required; the push is a read-only operation against SQLite.

13.2  Fibery Field Mapping
──────────────────────────

Fibery Field                              Source                         Notes
────────────────────────────────────────  ─────────────────────────────  ──────────────────────────────
Agreement Management/Time Log ID          time_entries.id                Upsert conflict key
Agreement Management/Start Date Time      time_entries.start_time        ISO-8601 with .000Z millis
Agreement Management/End Date Time        time_entries.end_time          Nullable; omitted if NULL
Agreement Management/Seconds              time_entries.duration          Integer seconds; nullable
Agreement Management/Clockify Hours       time_entries.duration / 3600   Float, 4 dp; nullable
Agreement Management/Task                 time_entries.description       Text; omitted if NULL
Agreement Management/Task ID              time_entries.task_id           Text; omitted if NULL
Agreement Management/Project ID           time_entries.project_id        Text; omitted if NULL
Agreement Management/Billable             1 → "Yes", 0 → "No"           Always present
Agreement Management/User ID              users.email                    Existing data uses email
Agreement Management/Time Entry User Name users.name                     JOIN from users table
Agreement Management/Time Entry Project   projects.name                  JOIN from projects table
  Name
Agreement Management/Clockify User        relation → Clockify Users      Matched by users.id;
                                                                         omitted if no match
Agreement Management/Agreement            relation → Agreements          Matched by project_id →
                                                                         Agreement.Clockify Project ID;
                                                                         omitted if no match

Fields intentionally NOT written (computed by Fibery formulas):
  Name, Hours, Cost, Clockify Bill Rate, Clockify Cost Rate, Agreement Name,
  Clockify User Manager, Clockify User Role, User Role, User Role Bill Rate,
  User Role Cost Rate.

13.3  Functional Requirements
──────────────────────────────
FR-34  The tool MUST store a Fibery API key in config.json as fibery_api_key
       and read it from the FIBERY_API_KEY environment variable (takes priority).
FR-35  The Settings screen MUST include a Fibery API key input field and a
       "Verify Fibery Connection" button that tests the key against the API.
FR-36  A "Push to Fibery" option MUST appear in the Main Menu (keyboard: f).
FR-37  The Fibery Push screen MUST display a single "Labor Costs" progress row
       with a progress bar, record counter (pushed/total), and status chip.
FR-38  Before pushing, the orchestrator MUST perform parallel pre-flight
       lookups to build:
         clockify_user_map  : Clockify User ID → Fibery UUID
         agreement_map      : Clockify Project ID → Fibery UUID (Agreement)
FR-39  Running timers (end_time IS NULL) MUST be skipped and counted in the
       "skipped" field of PushProgress; they MUST NOT be sent to Fibery.
FR-40  The push MUST use fibery.entity.batch/create-or-update with conflict-
       field = "Agreement Management/Time Log ID" and conflict-action =
       "update-latest" so the operation is idempotent.
FR-41  Entities MUST be sent in batches of FIBERY_BATCH_SIZE (50) to stay
       within Fibery request size limits.
FR-42  The Fibery client MUST use asyncio.Semaphore(3) to cap concurrent
       requests to ≤ 3 per second (Fibery rate limit).
FR-43  Entries whose project_id has no matching Agreement MUST still be pushed
       with the Agreement relation field omitted (not an error condition).
FR-44  Entries whose user_id has no matching Clockify User MUST still be pushed
       with text fields (User ID, User Name) populated and the relation omitted.
FR-45  The push log panel MUST surface: pre-flight result counts, running-timer
       skip count, per-batch progress, and a final summary on completion.

13.4  Fibery API Details
────────────────────────
Base URL  : https://harpin-ai.fibery.io
Auth      : Authorization: Token <api_key>
Endpoint  : POST /api/commands
Payload   : JSON array of command objects

Example batch upsert command:
  {
    "command": "fibery.entity.batch/create-or-update",
    "args": {
      "type": "Agreement Management/Labor Costs",
      "conflict-field": "Agreement Management/Time Log ID",
      "conflict-action": "update-latest",
      "entities": [ { "fibery/id": "<uuid4>", ... }, ... ]
    }
  }

Relations are embedded as: {"fibery/id": "<uuid>"}
Fibery returns HTTP 200 with a result array; non-200 responses indicate errors.

13.5  Push Orchestrator Flow
─────────────────────────────
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  FiberyPushOrchestrator.push_all(workspace_id)                          │
  │                                                                         │
  │  Step 0 — Pre-flight (parallel)                                         │
  │    asyncio.gather(get_clockify_user_map(), get_agreement_map())         │
  │    → clockify_user_map  { clockify_user_id: fibery_uuid }               │
  │    → agreement_map      { clockify_project_id: fibery_uuid }            │
  │                                                                         │
  │  Step 1 — Load SQLite                                                   │
  │    SELECT te.*, u.*, p.name FROM time_entries te                        │
  │      LEFT JOIN users u ON te.user_id = u.id                             │
  │      LEFT JOIN projects p ON te.project_id = p.id                      │
  │    Partition: complete_rows (end_time NOT NULL) vs skipped (running)    │
  │                                                                         │
  │  Step 2 — Build payloads                                                │
  │    For each complete row → LaborCostPayload → .to_fibery_entity()       │
  │    Resolve relations via clockify_user_map and agreement_map            │
  │                                                                         │
  │  Step 3 — Batch upsert                                                  │
  │    For each chunk of 50 payloads:                                       │
  │      await client.batch_upsert_labor_costs(entities)                   │
  │      progress.pushed += count; notify TUI callback                      │
  │                                                                         │
  │  Return PushProgress(total, pushed, skipped, errors, status)            │
  └─────────────────────────────────────────────────────────────────────────┘
