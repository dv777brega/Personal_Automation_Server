from datetime import timedelta
import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models.task import Schedule, Task, utcnow
from backend.app.services.task_service import script_path


def wait_task(client, task_id, status=None, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if (task["status"] == status if status else task["status"] not in {"PENDING", "RUNNING"}):
            return task
        time.sleep(0.03)
    raise AssertionError(f"Task did not reach {status or 'a terminal state'}: {task}")


def run(client, name="hello.py", **body):
    response = client.post(f"/api/scripts/{name}/run", json=body)
    assert response.status_code == 202, response.text
    return response.json()["task_id"]


def test_dashboard_health_and_discovery(make_server):
    client, _, _ = make_server()
    page = client.get("/")
    assert page.status_code == 200
    assert "A little less manual." in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/config").json()["timeout_seconds"] == 5
    assert {item["name"] for item in client.get("/api/scripts").json()["items"]} == {"hello.py", "fail.py", "slow.py"}
    assert client.get("/openapi.json").status_code == 200


def test_success_failure_arguments_and_history(make_server):
    client, _, settings = make_server()
    (settings.scripts_dir / "cwd.py").write_text("from pathlib import Path\nprint(Path.cwd())\n")
    task_id = run(client, arguments=["hello world", "$(touch nope)", ";echo nope"])
    success = wait_task(client, task_id)
    assert success["status"] == "COMPLETED"
    assert success["exit_code"] == 0
    assert "hello world $(touch nope) ;echo nope" in success["stdout"]
    assert success["started_at"].endswith("Z") and success["finished_at"].endswith("Z")
    assert success["arguments"][0] == "hello world"
    assert not (settings.scripts_dir / "nope").exists()
    failure = wait_task(client, run(client, "fail.py"))
    assert failure["status"] == "FAILED" and failure["exit_code"] == 7
    assert "problem" in failure["stderr"]
    cwd = wait_task(client, run(client, "cwd.py"))
    assert cwd["stdout"].strip() == str(settings.scripts_dir)
    listing = client.get("/api/tasks?limit=1&offset=1").json()
    assert listing["total"] == 3 and len(listing["items"]) == 1
    assert "stdout" not in listing["items"][0]
    assert client.get("/api/tasks?status=FAILED").json()["total"] == 1
    assert client.get("/api/stats").json()["by_status"] == {"COMPLETED": 2, "FAILED": 1}
    assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 409


def test_invalid_script_and_payloads(make_server, tmp_path):
    client, _, settings = make_server()
    assert client.post("/api/scripts/missing.py/run").status_code == 404
    assert client.post("/api/scripts/no.txt/run").status_code == 400
    for name in ["../hello.py", "/tmp/a.py", "sub/hello.py", "..\\hello.py"]:
        with pytest.raises(ValueError):
            script_path(settings.scripts_dir, name)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')")
    (settings.scripts_dir / "link.py").symlink_to(outside)
    assert client.post("/api/scripts/link.py/run").status_code == 404
    assert "link.py" not in str(client.get("/api/scripts").json())
    for body in [{"timeout_seconds": 0}, {"arguments": ["\x00"]}, {"arguments": [1]}, {"arguments": ["x"] * 51}, {"unknown": True}]:
        assert client.post("/api/scripts/hello.py/run", json=body).status_code == 422
    assert client.get("/api/tasks?limit=0").status_code == 422
    assert client.get("/api/tasks?status=UNKNOWN").status_code == 422
    assert client.get("/api/tasks/999").status_code == 404
    assert client.post("/api/tasks/999/cancel").status_code == 404
    assert client.get("/api/tasks").json()["total"] == 0


def test_timeout_cancel_and_bounded_queue(make_server):
    client, _, _ = make_server(max_workers=1, max_pending=1)
    first = run(client, "slow.py", timeout_seconds=1)
    wait_task(client, first, "RUNNING")
    second = run(client)
    assert client.get(f"/api/tasks/{second}").json()["started_at"] is None
    rejected = client.post("/api/scripts/hello.py/run")
    assert rejected.status_code == 429 and rejected.headers["retry-after"] == "2"
    timeout = wait_task(client, first)
    assert timeout["status"] == "TIMED_OUT" and "started" in timeout["stdout"]
    assert wait_task(client, second)["status"] == "COMPLETED"
    third = run(client, "slow.py")
    wait_task(client, third, "RUNNING")
    queued = run(client)
    assert client.post(f"/api/tasks/{queued}/cancel").status_code == 202
    assert client.post(f"/api/tasks/{third}/cancel").status_code == 202
    assert wait_task(client, third)["status"] == "CANCELLED"
    assert wait_task(client, queued)["status"] == "CANCELLED"


def test_output_cap_and_unicode(make_server):
    client, _, settings = make_server(max_output_bytes=1024)
    (settings.scripts_dir / "loud.py").write_text("import sys\nprint('x'*50000)\nprint('y'*50000, file=sys.stderr)\n")
    task = wait_task(client, run(client, "loud.py"))
    assert task["status"] == "COMPLETED"
    assert len(task["stdout"]) < 1200 and "truncated" in task["stdout"]
    assert len(task["stderr"]) < 1200 and "truncated" in task["stderr"]
    (settings.scripts_dir / "unicode.py").write_text("print('Привет 🌱')\n", encoding="utf-8")
    assert "Привет 🌱" in wait_task(client, run(client, "unicode.py"))["stdout"]


def test_authentication_and_browser_boundaries(make_server, monkeypatch):
    monkeypatch.setenv("PAS_API_TOKEN", "secret")
    client, _, settings = make_server(api_token="secret")
    assert client.get("/api/health").json()["authentication_required"] is True
    assert client.get("/").status_code == 200
    assert client.get("/api/tasks").status_code == 401
    assert client.post("/api/scripts/hello.py/run").status_code == 401
    client.headers["Authorization"] = "Bearer secret"
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/api/tasks", headers={"Origin": "http://evil.test"}).status_code == 403
    assert client.get("/api/tasks", headers={"Origin": "http://[broken"}).status_code == 403
    assert client.post("/api/scripts/hello.py/run", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/tasks", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "evil.test"}).status_code == 400
    (settings.scripts_dir / "env.py").write_text("import os\nprint(os.getenv('PAS_API_TOKEN', 'absent'))\n")
    assert wait_task(client, run(client, "env.py"))["stdout"].strip() == "absent"


def make_due(app, schedule_id):
    with app.state.sessions.begin() as db:
        db.get(Schedule, schedule_id).next_run_at = utcnow() - timedelta(seconds=1)


def test_schedule_dispatch_pause_resume_and_delete(make_server):
    client, app, _ = make_server()
    response = client.post("/api/schedules", json={"name": "Report", "script_name": "hello.py", "interval_seconds": 10, "arguments": ["scheduled"]})
    assert response.status_code == 201
    schedule_id = response.json()["id"]
    assert client.get("/api/tasks").json()["total"] == 0
    make_due(app, schedule_id)
    app.state.runner.tick()
    schedule = client.get("/api/schedules").json()["items"][0]
    task = wait_task(client, schedule["last_task_id"])
    assert task["schedule_id"] == schedule_id and "scheduled" in task["stdout"]
    assert client.patch(f"/api/schedules/{schedule_id}", json={"enabled": False}).status_code == 200
    make_due(app, schedule_id)
    app.state.runner.tick()
    assert client.get("/api/tasks").json()["total"] == 1
    assert client.patch(f"/api/schedules/{schedule_id}", json={"enabled": True}).json()["enabled"]
    app.state.runner.tick()
    assert client.get("/api/tasks").json()["total"] == 1
    assert client.delete(f"/api/schedules/{schedule_id}").status_code == 204
    assert client.get("/api/schedules").json()["items"] == []
    assert client.get(f"/api/tasks/{task['task_id']}").status_code == 200
    assert client.patch("/api/schedules/999", json={"enabled": False}).status_code == 404
    assert client.delete("/api/schedules/999").status_code == 404


def test_schedule_no_overlap_and_missing_script(make_server):
    client, app, settings = make_server()
    response = client.post("/api/schedules", json={"name": "Slow", "script_name": "slow.py", "interval_seconds": 10})
    schedule_id = response.json()["id"]
    make_due(app, schedule_id)
    app.state.runner.tick()
    make_due(app, schedule_id)
    app.state.runner.tick()
    assert client.get("/api/tasks").json()["total"] == 1
    task_id = client.get("/api/tasks").json()["items"][0]["task_id"]
    client.post(f"/api/tasks/{task_id}/cancel")
    wait_task(client, task_id)
    (settings.scripts_dir / "slow.py").unlink()
    app.state.runner.tick()
    assert "Script not found" in client.get("/api/schedules").json()["items"][0]["last_error"]
    for fields in [{"interval_seconds": 0}, {"name": "  "}, {"script_name": "missing.py"}]:
        body = {"name": "A", "script_name": "hello.py", "interval_seconds": 10, **fields}
        assert client.post("/api/schedules", json=body).status_code in {404, 422}


def test_single_instance_lock(make_server):
    _, _, settings = make_server()
    with pytest.raises(RuntimeError, match="already has a running server"):
        with TestClient(create_app(settings)):
            pass


def test_old_database_and_restart_recovery(tmp_path):
    database = tmp_path / "automation.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, script_name VARCHAR NOT NULL, status VARCHAR, pid INTEGER, exit_code INTEGER, stdout VARCHAR, stderr VARCHAR, started_at DATETIME, finished_at DATETIME)")
        db.execute("INSERT INTO tasks (script_name, status, stdout) VALUES ('old.py', 'COMPLETED', 'old output')")
        db.execute("INSERT INTO tasks (script_name, status) VALUES ('old.py', 'RUNNING')")
        db.execute("INSERT INTO tasks (script_name, status) VALUES ('old.py', 'PENDING')")
    settings = Settings(scripts_dir=tmp_path / "scripts", database_path=database, allowed_hosts=("testserver",))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/tasks/1").json()["stdout"] == "old output"
        for task_id in (2, 3):
            assert client.get(f"/api/tasks/{task_id}").json()["status"] == "INTERRUPTED"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/tasks").json()["total"] == 3


def test_graceful_shutdown_cancels_and_persists(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "slow.py").write_text("import time\ntime.sleep(30)\n")
    settings = Settings(scripts_dir=scripts, database_path=tmp_path / "db", allowed_hosts=("testserver",))
    app = create_app(settings)
    with TestClient(app) as client:
        task_id = run(client, "slow.py")
        wait_task(client, task_id, "RUNNING")
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/tasks/{task_id}").json()["status"] == "CANCELLED"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_timeout_kills_child_process(make_server):
    client, _, settings = make_server()
    marker = settings.scripts_dir / "escaped.txt"
    source = "import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', \"import time; from pathlib import Path; time.sleep(2); Path('escaped.txt').write_text('bad')\"])\ntime.sleep(30)\n"
    (settings.scripts_dir / "child.py").write_text(source)
    task = wait_task(client, run(client, "child.py", timeout_seconds=1))
    assert task["status"] == "TIMED_OUT"
    time.sleep(1.3)
    assert not marker.exists()
