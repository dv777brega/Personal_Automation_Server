from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utcnow():
    # SQLite stores naive datetimes; API serialization explicitly adds UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    script_name = Column(String, nullable=False)
    status = Column(String, default="PENDING", nullable=False, index=True)
    pid = Column(Integer, nullable=True)
    exit_code = Column(Integer, nullable=True)
    stdout = Column(String, nullable=True)
    stderr = Column(String, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    # Keep the original schema intact; extra run metadata lives in its own table.


class TaskOptions(Base):
    __tablename__ = "task_options"

    task_id = Column(Integer, primary_key=True)
    arguments = Column(JSON, nullable=False, default=list)
    timeout_seconds = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    schedule_id = Column(Integer, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    script_name = Column(String, nullable=False)
    arguments = Column(JSON, nullable=False, default=list)
    timeout_seconds = Column(Integer, nullable=False)
    interval_seconds = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    next_run_at = Column(DateTime, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    last_task_id = Column(Integer, nullable=True)
    last_error = Column(String, nullable=True)
