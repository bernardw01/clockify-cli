# clockify-cli

A local-first terminal tool that mirrors your [Clockify](https://clockify.me) workspace into a SQLite database and lets you explore it through a full interactive TUI — no browser required.

> **Full product specification:** [`docs/clockify-sync.prd`](docs/clockify-sync.prd)

---

## What it does

| Capability | Detail |
|---|---|
| **Sync** | Downloads clients, projects, users, and all time entries for a workspace |
| **Incremental or Full** | Incremental mode fetches only records newer than the last sync; Full re-fetches everything |
| **Local SQLite store** | All data is written to `~/.local/share/clockify-cli/clockify.db` — queryable with any SQL tool |
| **Live TUI progress** | Per-entity progress bars, record counters, and status chips update in real time during sync |
| **Time entry browser** | Searchable table of entries with project, description, duration, and billable flag |
| **Structured logging** | Every API request and response is logged to `~/.local/share/clockify-cli/logs/` |

---

## Screenshots

```
┌─────────────────────────────────────────┐
│  Clockify CLI                  12:34:56 │
├─────────────────────────────────────────┤
│  Sync Data                              │
│  Mode: Incremental                      │
│                                         │
│  [Start Sync]  [Mode: Incremental]  [Back] │
│                                         │
│  Clients      ████████████ 100%  12/12  done  │
│  Projects     ████████████ 100%  34/34  done  │
│  Users        ████████████ 100%   4/4   done  │
│  Time Entries ███████████▌  97% 480/493 syncing │
│                                         │
│  12:34:51  Starting incremental sync... │
│  12:34:52  Synced 12 clients            │
└─────────────────────────────────────────┘
```

---

## Requirements

- macOS 13+
- Python 3.12 (install via `~/.local/bin/uv python install 3.12`)
- [`uv`](https://docs.astral.sh/uv/) package manager
- A [Clockify API key](https://app.clockify.me/user/settings) (free account works)

---

## Installation

```bash
# Clone the repo
git clone <your-repo-url> clockify-cli
cd clockify-cli

# Install (non-editable — required for iCloud Drive paths)
make install

# Launch
make run
```

> **After any code change**, run `make reinstall` before re-launching so the updated files are copied into the virtual environment.

---

## First run

1. The TUI detects you are unconfigured and opens **Settings** automatically.
2. Paste your Clockify API key — the workspace list populates from the API.
3. Select your workspace and press **Save**.
4. You are taken to the **Main Menu**.

Alternatively, set the environment variable to skip the API key field:

```bash
export CLOCKIFY_API_KEY="your-key-here"
~/.local/bin/uv run clockify-cli
```

---

## Usage

### Main Menu

| Option | Action |
|---|---|
| Sync Data | Open the sync screen |
| View Time Entries | Browse and search local time entries |
| Settings | Change API key or workspace |
| Quit | Exit the TUI |

### Sync Screen

| Control | Action |
|---|---|
| **Start Sync `[s]`** | Begin syncing in the current mode |
| **Mode button `[i]`** | Toggle between Incremental and Full sync |
| **Escape** | Return to Main Menu |

### Time Entries Screen

Type to search (debounced 300 ms) across description, project name, and user name.  Press **Escape** to go back.

---

## Project layout

```
clockify_cli/
├── main.py              Entry point — logging setup, TUI launch
├── config.py            Config load/save (~/.config/clockify-cli/config.json)
├── constants.py         BASE_URL, file paths, rate-limit constant
├── api/
│   ├── client.py        Async HTTP client — rate limiting, request/response logging
│   ├── models.py        Pydantic v2 models for all API responses
│   └── exceptions.py    Typed exception hierarchy
├── db/
│   ├── database.py      Async SQLite wrapper (WAL mode, FK enforcement)
│   ├── schema.py        DDL for all 7 tables + indexes
│   └── repositories/    One repository class per entity type
├── sync/
│   ├── orchestrator.py  Coordinates the full sync pipeline
│   └── progress.py      EntityProgress / SyncProgress dataclasses
└── tui/
    ├── app.py           ClockifyApp — DB lifecycle, screen routing
    ├── styles.tcss      Textual CSS
    └── screens/         MainMenu, Settings, SyncScreen, TimeEntries
```

---

## Data model

```
workspaces
    └── clients          (FK → workspaces)
    └── projects         (FK → workspaces, clients)
    └── users            (FK → workspaces)
    └── time_entries     (FK → workspaces, users, projects)
    └── sync_log         last sync status per entity type
```

The database lives at `~/.local/share/clockify-cli/clockify.db` and can be queried directly:

```sql
-- Hours per project this month
SELECT p.name, ROUND(SUM(te.duration) / 3600.0, 1) AS hours
FROM time_entries te
JOIN projects p ON p.id = te.project_id
WHERE te.start_time >= date('now', 'start of month')
GROUP BY p.name
ORDER BY hours DESC;
```

---

## Development

```bash
# Run tests
make test

# Lint
make lint

# Full reinstall after code changes
make reinstall
```

Tests use `httpx.MockTransport` for API calls — no real network required.  All async tests run with `pytest-asyncio` in auto mode.

```
tests/
├── test_config.py              7 tests
├── api/test_client.py         16 tests
├── db/test_database.py         6 tests
├── db/test_repositories.py    21 tests
└── sync/test_orchestrator.py   9 tests
                              ──────────
                              63 tests total
```

---

## Configuration files

| Path | Purpose |
|---|---|
| `~/.config/clockify-cli/config.json` | API key, workspace ID, last sync timestamp |
| `~/.local/share/clockify-cli/clockify.db` | SQLite database |
| `~/.local/share/clockify-cli/logs/` | Rotating log files (10 MB × 5) |

The config file is created with `chmod 600` — your API key is never logged (only the last 4 characters appear in log output).

---

## Tech stack

| Layer | Library | Version |
|---|---|---|
| TUI | [Textual](https://textual.textualize.io) | ≥ 0.86 |
| HTTP | [httpx](https://www.python-httpx.org) | ≥ 0.27 |
| Validation | [Pydantic v2](https://docs.pydantic.dev) | ≥ 2.7 |
| Database | [aiosqlite](https://aiosqlite.omnilib.dev) | ≥ 0.20 |
| Logging | [loguru](https://loguru.readthedocs.io) | ≥ 0.7 |
| Packaging | [uv](https://docs.astral.sh/uv/) + hatchling | — |

---

## Further reading

See [`docs/clockify-sync.prd`](docs/clockify-sync.prd) for the full product requirements document, including functional requirements, acceptance criteria, known constraints, and planned future enhancements.
