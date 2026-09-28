# Personal Automation Server

A local dashboard for running Python automations and scheduling repeat work. FastAPI serves the dashboard and API; SQLite keeps your task history and schedules. No frontend build or external database is required.

## Start here

Python **3.11+** is required. Run these commands from the project directory.

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m backend.app
```

On Ubuntu/Debian, if creating the virtual environment fails:

```bash
sudo apt update
sudo apt install python3-pip python3-venv
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m backend.app
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)**. Choose `disk_space.py` and click **Run script**. Its task details show the output and exit code. Stop the server with **Ctrl+C**.

**[Read the step-by-step usage tutorial →](docs/TUTORIAL.md)**

## Included Windows automations

| Script | What it does | Dashboard arguments |
| --- | --- | --- |
| `disk_space.py` | Check disk space; fail below a free-space threshold | `["--path", "C:/", "--min-free-gb", "10"]` |
| `backup_folder.py` | Create a dated ZIP without changing the source | `["--source", "C:/Users/YourName/Documents"]` |
| `organize_downloads.py` | Preview sorting Downloads into file-type folders | `[]`; add `["--apply"]` to move files |
| `open_websites.py` | Open your sites in the Windows default browser | `["https://example.com", "https://www.wikipedia.org"]` |

Replace `YourName` with your Windows username. These scripts use only Python's standard library. File operations also work on Linux/macOS. Browser tabs open on the machine running the server, in its signed-in desktop session. See the [Windows automation walkthrough](docs/TUTORIAL.md#2-use-the-included-windows-automations) for options and scheduling examples.

## What it does

- A responsive dashboard with script discovery, task counts, filtered history, and output inspection.
- Background runs with arguments, a bounded queue, configurable concurrency, timeouts, cancellation, and reruns.
- Persistent interval schedules with pause/resume and no overlapping runs of the same schedule.
- SQLite task history, UTC timestamps, stdout/stderr capture, and interrupted-task recovery.
- Local-only defaults, optional bearer-token authentication, host validation, and cross-origin request rejection.
- Interactive API documentation at `/docs`; automated integration tests in `tests/`.

Scripts are trusted local programs: they run as your user, with your virtual environment's Python, and with `scripts/` as their working directory. This is not a sandbox or a multi-user job platform. Output is available after a task finishes, capped at 1 MB per stream by default.

## Configuration

Export environment variables **before** launching. A `.env` file is not loaded automatically. See [configuration and remote access](docs/TUTORIAL.md#configuration).

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAS_SCRIPTS_DIR` | `<project>/scripts` | Directory containing runnable `.py` files |
| `PAS_DATABASE_PATH` | `<project>/automation.db` | SQLite database location |
| `PAS_MAX_WORKERS` | `2` | Concurrent running scripts |
| `PAS_MAX_PENDING` | `20` | Additional queue capacity |
| `PAS_TIMEOUT_SECONDS` | `300` | Default run timeout in seconds |
| `PAS_MAX_OUTPUT_BYTES` | `1000000` | Captured bytes per output stream per task |
| `PAS_API_TOKEN` | unset | Require `Authorization: Bearer <token>` for task/config APIs |
| `PAS_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Comma-separated accepted HTTP hostnames/IPs |

Run exactly **one server worker per database**. A file lock prevents accidental duplicate runners. Use `python -m backend.app --port 8001` to change the port. Development alternative: `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`. Reloading stops active work; avoid it while running automations.

## API overview

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health/version/authentication requirement |
| GET | `/api/config` | Public execution settings (no secrets or paths) |
| GET | `/api/scripts` | List available scripts |
| POST | `/api/scripts/{filename}/run` | Queue a run; returns **202** and `task_id` |
| GET | `/api/tasks?limit=25&offset=0&status=FAILED` | Paginated history; status is optional |
| GET | `/api/tasks/{id}` | Task details and captured output |
| POST | `/api/tasks/{id}/cancel` | Request cancellation |
| GET | `/api/stats` | Counts by task status |
| GET / POST | `/api/schedules` | List / create interval schedules |
| PATCH | `/api/schedules/{id}` | Enable or pause a schedule |
| DELETE | `/api/schedules/{id}` | Delete a schedule; preserve task history |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/scripts/disk_space.py/run \
  -H 'Content-Type: application/json' \
  -d '{"arguments":["--min-free-gb","10"],"timeout_seconds":30}'
curl http://127.0.0.1:8000/api/tasks/1
```

Use the ID from the first response. With authentication enabled, add `-H "Authorization: Bearer $PAS_API_TOKEN"` to API requests. The run endpoint now responds immediately with a queued task instead of waiting for the script to finish; clients of the original prototype should poll task details.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use temporary scripts and databases. They exercise real subprocess execution, queue limits, cancellation, timeouts, child-process cleanup on Linux, authentication, schedules, persistence, and compatibility with the original task table. The app uses plain HTML/CSS/JavaScript, served from `backend/app/static/`.

`constraints.txt` pins the tested dependency versions and is applied automatically by the requirements files. Update it deliberately when upgrading dependencies.

For optional desktop/mobile browser verification:

```bash
python -m pip install playwright
python -m playwright install chromium
python tools/browser_smoke.py
```

The browser check starts temporary local servers with isolated databases and cleans them up afterward. Playwright is not required to run the application.

```text
backend/app/
  __main__.py           Launch command
  config.py             Environment configuration
  database.py           SQLite setup
  instance_lock.py      Single-runner protection
  main.py               API, authentication, and app lifecycle
  models/task.py        Task and schedule tables
  services/task_service.py  Runner and scheduler
  static/               Browser dashboard
scripts/                Your trusted Python automations
tests/                 Integration tests
docs/TUTORIAL.md       Usage walkthrough and operations guide
```

## Operational limits

The server must remain running for schedules to execute. Missed intervals are coalesced into one run after restart, rather than replayed. Scheduling is best effort, not exactly-once: a hard crash during dispatch can leave an interrupted task or cause a repeat after restart. Make scripts safe to rerun when possible.

Graceful shutdown cancels active and queued tasks. An abrupt server/process crash marks unfinished database records `INTERRUPTED` on the next start; it cannot undo script side effects or guarantee termination of orphan processes. POSIX process groups are cleaned up on normal completion, timeout, and cancellation. Detached child processes are unsupported; Windows uses `taskkill` for active process trees and has weaker descendant cleanup after the parent exits.

History is retained indefinitely. Back up the database and monitor disk usage; see the tutorial. Existing task rows in the configured database are preserved. The original prototype had inconsistent paths and may have placed a database outside this project; if needed, stop both versions and point `PAS_DATABASE_PATH` to that file before launching.
