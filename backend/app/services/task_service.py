"""Bounded, durable task execution for a single server process."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from sqlalchemy import select, update
from backend.app.models.task import Schedule, Task, TaskOptions, utcnow

log = logging.getLogger(__name__)
TERMINAL_STATES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"}


class QueueFull(Exception):
    pass


def script_path(scripts_dir: Path, name: str) -> Path:
    # Only immediate, regular .py files; symlinks and traversal are not executable.
    if not name or name != Path(name).name or "/" in name or "\\" in name or not name.endswith(".py"):
        raise ValueError("Use the filename of a Python script in the scripts directory")
    path = scripts_dir / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != scripts_dir.resolve():
        raise FileNotFoundError(f"Script not found: {name}")
    return path.resolve()


def iso(value):
    return value.isoformat(timespec="milliseconds") + "Z" if value else None


def task_data(task, options=None, include_output=True):
    data = {
        "task_id": task.id, "script_name": task.script_name, "status": task.status,
        "pid": task.pid, "exit_code": task.exit_code,
        "created_at": iso(options.created_at if options else task.started_at),
        "started_at": iso(task.started_at), "finished_at": iso(task.finished_at),
        "arguments": options.arguments if options else [],
        "timeout_seconds": options.timeout_seconds if options else None,
        "schedule_id": options.schedule_id if options else None,
    }
    if include_output:
        data.update(stdout=task.stdout or "", stderr=task.stderr or "")
    return data


def schedule_data(schedule):
    return {key: iso(value) if key.endswith("_at") else value for key, value in (
        (column.name, getattr(schedule, column.name)) for column in Schedule.__table__.columns
    )}


class OutputCapture:
    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit
        self.data = bytearray()
        self.truncated = False
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            while chunk := self.stream.read(8192):
                remaining = self.limit - len(self.data)
                self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        except (OSError, ValueError):
            pass
        finally:
            self.stream.close()

    def result(self):
        # A detached descendant may retain a pipe; never let it block the runner.
        self.thread.join(timeout=1)
        text = bytes(self.data).decode("utf-8", errors="replace")
        if self.truncated:
            text += "\n[Output truncated at configured byte limit]"
        if self.thread.is_alive():
            text += "\n[Output pipe still open in a detached child; capture stopped]"
        return text


def kill_process(process):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            # taskkill terminates the process tree on Windows.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if process.poll() is None:
                process.kill()
    except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()


class TaskRunner:
    def __init__(self, settings, sessions):
        self.settings = settings
        self.sessions = sessions
        self.lock = threading.RLock()
        self.controls = {}
        self.stopping = False
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="automation")
        self.scheduler = threading.Thread(target=self._schedule_loop, name="scheduler", daemon=True)

    def start(self):
        with self.sessions.begin() as db:
            db.execute(update(Task).where(Task.status.in_(["PENDING", "RUNNING"])).values(
                status="INTERRUPTED", finished_at=utcnow(),
                stderr="Server stopped before this task finished. Review side effects before rerunning."
            ))
        self.scheduler.start()

    def submit(self, name, arguments, timeout, schedule_id=None):
        script_path(self.settings.scripts_dir, name)
        with self.lock:
            if self.stopping or len(self.controls) >= self.settings.max_workers + self.settings.max_pending:
                raise QueueFull("Task queue is full or the server is shutting down; try again later")
            with self.sessions.begin() as db:
                task = Task(script_name=name, status="PENDING", started_at=None)
                db.add(task)
                db.flush()
                # SQLAlchemy's column default otherwise assigns a start time during INSERT.
                task.started_at = None
                options = TaskOptions(task_id=task.id, arguments=arguments, timeout_seconds=timeout,
                                      schedule_id=schedule_id)
                db.add(options)
                db.flush()
                result = task_data(task, options)
            cancel = threading.Event()
            self.controls[task.id] = cancel
            self.executor.submit(self._run, task.id, name, arguments, timeout, cancel)
            return result

    def cancel(self, task_id):
        with self.lock:
            event = self.controls.get(task_id)
            if event:
                event.set()
                return True
            return False

    def close(self):
        with self.lock:
            self.stopping = True
            self.stop_event.set()
            for event in self.controls.values():
                event.set()
        if self.scheduler.is_alive():
            self.scheduler.join()
        self.executor.shutdown(wait=True)

    def _run(self, task_id, name, arguments, timeout, cancel):
        process = None
        stdout = stderr = None
        status, exit_code, out, err = "FAILED", None, "", ""
        try:
            if cancel.is_set():
                status = "CANCELLED"
                return
            path = script_path(self.settings.scripts_dir, name)
            # API credentials are server credentials, not script environment variables.
            environment = {k: v for k, v in os.environ.items() if k != "PAS_API_TOKEN"}
            environment.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            process = subprocess.Popen(
                [sys.executable, "-u", str(path), *arguments], cwd=self.settings.scripts_dir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                env=environment, start_new_session=(os.name == "posix"),
            )
            stdout = OutputCapture(process.stdout, self.settings.max_output_bytes)
            stderr = OutputCapture(process.stderr, self.settings.max_output_bytes)
            with self.sessions.begin() as db:
                task = db.get(Task, task_id)
                task.status, task.pid, task.started_at = "RUNNING", process.pid, utcnow()
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancel.wait(0.1):
                    status = "CANCELLED"
                    break
                if time.monotonic() >= deadline:
                    status = "TIMED_OUT"
                    break
            else:
                status = "COMPLETED" if process.returncode == 0 else "FAILED"
            # Clean up remaining children even when the main script exits normally.
            kill_process(process)
            exit_code = process.wait(timeout=10)
            out, err = stdout.result(), stderr.result()
            if status == "TIMED_OUT":
                err += f"\nTask exceeded its {timeout}-second timeout."
        except Exception as exc:
            log.exception("Task %s failed", task_id)
            if process:
                kill_process(process)
                try:
                    exit_code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            out = stdout.result() if stdout else ""
            err = (stderr.result() if stderr else "") + f"\n{type(exc).__name__}: {exc}"
        finally:
            try:
                with self.sessions.begin() as db:
                    task = db.get(Task, task_id)
                    task.status, task.exit_code = status, exit_code
                    task.stdout, task.stderr, task.finished_at = out, err, utcnow()
            finally:
                with self.lock:
                    self.controls.pop(task_id, None)

    def _schedule_loop(self):
        while not self.stop_event.wait(1):
            try:
                self.tick()
            except Exception:
                log.exception("Scheduler tick failed")

    def tick(self):
        # Serialize edits and dispatch so disabling a schedule takes effect immediately.
        with self.lock, self.sessions() as db:
            due = db.scalars(select(Schedule).where(
                Schedule.enabled.is_(True), Schedule.next_run_at <= utcnow()
            )).all()
            for schedule in due:
                try:
                    # Do not overlap runs of the same schedule.
                    if schedule.last_task_id in self.controls:
                        continue
                    task = self.submit(schedule.script_name, schedule.arguments,
                                       schedule.timeout_seconds, schedule.id)
                except QueueFull:
                    # Leave it due; retry when capacity becomes available.
                    continue
                except (ValueError, FileNotFoundError) as exc:
                    schedule.last_error = str(exc)
                    schedule.next_run_at = utcnow() + timedelta(seconds=schedule.interval_seconds)
                else:
                    schedule.last_task_id = task["task_id"]
                    schedule.last_run_at = utcnow()
                    schedule.next_run_at = utcnow() + timedelta(seconds=schedule.interval_seconds)
                    schedule.last_error = None
                db.commit()
