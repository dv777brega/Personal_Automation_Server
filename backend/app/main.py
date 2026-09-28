"""HTTP API and dashboard. Start with python -m backend.app."""
from contextlib import asynccontextmanager
from datetime import timedelta
import secrets
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import defer
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.config import Settings
from backend.app.database import create_database
from backend.app.instance_lock import InstanceLock
from backend.app.models.task import Schedule, Task, TaskOptions, utcnow
from backend.app.services.task_service import (
    QueueFull, TaskRunner, script_path, task_data, schedule_data,
)

STATIC_DIR = Path(__file__).parent / "static"
TaskStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: list[str] = Field(default_factory=list, max_length=50)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86400)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, arguments):
        if any(len(arg) > 4096 or "\x00" in arg for arg in arguments):
            raise ValueError("Each argument must be at most 4096 characters with no null bytes")
        return arguments


class ScheduleRequest(RunRequest):
    name: str = Field(min_length=1, max_length=100)
    script_name: str = Field(min_length=1, max_length=255)
    interval_seconds: int = Field(ge=10, le=31536000)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, name):
        if not name.strip():
            raise ValueError("Name must not be blank")
        return name.strip()


class ScheduleToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        instance_lock = InstanceLock(Path(str(settings.database_path) + ".lock"))
        instance_lock.acquire()
        engine = runner = None
        try:
            settings.scripts_dir.mkdir(parents=True, exist_ok=True)
            engine, sessions = create_database(settings.database_path)
            runner = TaskRunner(settings, sessions)
            app.state.sessions, app.state.runner = sessions, runner
            runner.start()
            yield
        finally:
            if runner:
                runner.close()
            if engine:
                engine.dispose()
            instance_lock.release()

    app = FastAPI(title="Personal Automation Server", version="1.0.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                except ValueError:
                    return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
                if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                    return JSONResponse({"detail": "Cross-origin API requests are not allowed"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site API requests are not allowed"}, status_code=403)
            if settings.api_token:
                authorization = request.headers.get("authorization", "")
                if not secrets.compare_digest(authorization.encode(), ("Bearer " + settings.api_token).encode()):
                    return JSONResponse({"detail": "A valid API token is required"}, status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def validate_script(name):
        try:
            return script_path(settings.scripts_dir, name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health_check():
        with app.state.sessions() as db:
            db.execute(select(1))
        return {"status": "ok", "version": app.version, "authentication_required": bool(settings.api_token)}

    @app.get("/api/config")
    def config():
        return {"max_workers": settings.max_workers, "max_pending": settings.max_pending,
                "timeout_seconds": settings.timeout_seconds, "max_output_bytes": settings.max_output_bytes}

    @app.get("/api/scripts")
    def list_scripts():
        scripts = []
        for path in sorted(settings.scripts_dir.glob("*.py")):
            try:
                validated = script_path(settings.scripts_dir, path.name)
                scripts.append({"name": path.name, "size_bytes": validated.stat().st_size})
            except (ValueError, OSError):
                continue
        return {"items": scripts}

    @app.post("/api/scripts/{script_name}/run", status_code=202)
    def run_script_endpoint(script_name: str, body: RunRequest | None = None):
        validate_script(script_name)
        body = body or RunRequest()
        try:
            return app.state.runner.submit(script_name, body.arguments,
                                           body.timeout_seconds or settings.timeout_seconds)
        except QueueFull as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "2"}) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/tasks")
    def list_tasks(limit: Annotated[int, Query(ge=1, le=100)] = 25,
                   offset: Annotated[int, Query(ge=0)] = 0, status: TaskStatus | None = None):
        with app.state.sessions() as db:
            query = select(Task, TaskOptions).outerjoin(
                TaskOptions, TaskOptions.task_id == Task.id
            ).options(defer(Task.stdout), defer(Task.stderr))
            count = select(func.count()).select_from(Task)
            if status:
                query, count = query.where(Task.status == status), count.where(Task.status == status)
            rows = db.execute(query.order_by(Task.id.desc()).offset(offset).limit(limit)).all()
            return {"items": [task_data(task, options, False) for task, options in rows],
                    "total": db.scalar(count), "limit": limit, "offset": offset}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int):
        with app.state.sessions() as db:
            task = db.get(Task, task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            return task_data(task, db.get(TaskOptions, task_id))

    @app.post("/api/tasks/{task_id}/cancel", status_code=202)
    def cancel_task(task_id: int):
        with app.state.sessions() as db:
            if not db.get(Task, task_id):
                raise HTTPException(404, "Task not found")
        if not app.state.runner.cancel(task_id):
            raise HTTPException(409, "Task has already finished")
        return {"task_id": task_id, "cancellation_requested": True}

    @app.get("/api/stats")
    def stats():
        with app.state.sessions() as db:
            counts = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
            return {"total": sum(counts.values()), "by_status": counts}

    @app.get("/api/schedules")
    def list_schedules():
        with app.state.sessions() as db:
            return {"items": [schedule_data(s) for s in db.scalars(select(Schedule).order_by(Schedule.id.desc()))]}

    @app.post("/api/schedules", status_code=201)
    def create_schedule(body: ScheduleRequest):
        validate_script(body.script_name)
        with app.state.runner.lock, app.state.sessions.begin() as db:
            values = body.model_dump()
            values["timeout_seconds"] = body.timeout_seconds or settings.timeout_seconds
            schedule = Schedule(**values, next_run_at=utcnow() + timedelta(seconds=body.interval_seconds))
            db.add(schedule)
            db.flush()
            return schedule_data(schedule)

    @app.patch("/api/schedules/{schedule_id}")
    def toggle_schedule(schedule_id: int, body: ScheduleToggle):
        with app.state.runner.lock, app.state.sessions.begin() as db:
            schedule = db.get(Schedule, schedule_id)
            if not schedule:
                raise HTTPException(404, "Schedule not found")
            if body.enabled and not schedule.enabled:
                validate_script(schedule.script_name)
                schedule.next_run_at = utcnow() + timedelta(seconds=schedule.interval_seconds)
                schedule.last_error = None
            schedule.enabled = body.enabled
            db.flush()
            return schedule_data(schedule)

    @app.delete("/api/schedules/{schedule_id}", status_code=204)
    def delete_schedule(schedule_id: int):
        with app.state.runner.lock, app.state.sessions.begin() as db:
            schedule = db.get(Schedule, schedule_id)
            if not schedule:
                raise HTTPException(404, "Schedule not found")
            db.delete(schedule)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
