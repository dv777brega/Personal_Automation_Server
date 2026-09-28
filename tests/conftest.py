from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def make_server(tmp_path):
    clients = []
    counter = 0

    def factory(**overrides):
        nonlocal counter
        counter += 1
        root = tmp_path / str(counter)
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "hello.py").write_text("import sys\nprint('hello', *sys.argv[1:])\n", encoding="utf-8")
        (scripts / "fail.py").write_text("import sys\nprint('problem', file=sys.stderr)\nsys.exit(7)\n", encoding="utf-8")
        (scripts / "slow.py").write_text("import time\nprint('started', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
        values = dict(scripts_dir=scripts, database_path=root / "automation.db",
                      allowed_hosts=("testserver",), timeout_seconds=5)
        values.update(overrides)
        settings = Settings(**values)
        app = create_app(settings)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client, app, settings

    yield factory
    for client in reversed(clients):
        client.__exit__(None, None, None)
