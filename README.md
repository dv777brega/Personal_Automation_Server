# Personal Automation Server

A lightweight FastAPI service for running automation scripts from the `scripts/` folder and tracking each task in a SQLite database.

## Requirements

- Python 3.10+
- pip
- A terminal with PowerShell or bash

## Quick start

1. Open a terminal in the project root.
2. Create and activate a virtual environment:

   PowerShell:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

   Bash/macOS/Linux:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Start the backend server:

   ```bash
   python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

5. Open the app in your browser or API client at:

   ```text
   http://localhost:8000
   ```

## API endpoints

- Health check:
  ```http
  GET /api/health
  ```

  Example response:
  ```json
  { "status": "ok!" }
  ```

- Run a script:
  ```http
  POST /api/scripts/{script_name}/run
  ```

  Example:
  ```http
  POST /api/scripts/test.py/run
  ```

  The server looks for scripts inside the `scripts/` folder and runs them with Python.

## Project behavior

- The app creates the SQLite database automatically at the project root as `automation.db`.
- Tasks are stored in the `tasks` table and include status, exit code, stdout, and stderr.
- Scripts should be placed in the `scripts/` directory and referenced by filename.

## Useful commands

- Start without reloader:
  ```bash
  python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
  ```

- Stop the server:
  ```text
  Ctrl + C
  ```

## Troubleshooting

- If imports fail, make sure the virtual environment is active and dependencies were installed.
- If the server cannot find a script, confirm the file exists in the `scripts/` directory.
- If the database is missing, restart the app; it will be created automatically.
