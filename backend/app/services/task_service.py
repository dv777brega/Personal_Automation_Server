import subprocess
import os
from backend.app.database import SessionLocal
from backend.app.models.task import Task

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def run_script(script_name: str):
    db = SessionLocal()

    task = Task(script_name=script_name, status="RUNNING")
    db.add(task)
    db.commit()
    db.refresh(task)

    script_path = os.path.join(PROJECT_ROOT, "scripts", script_name)
    result = subprocess.run(
        ["python", script_path],
        capture_output=True,
        text=True
    )

    task.status = "COMPLETED" if result.returncode == 0 else "FAILED"
    task.exit_code = result.returncode
    task.stdout = result.stdout
    task.stderr = result.stderr
    db.commit()

    task_data = {
        "task_id": task.id,
        "status": task.status,
        "exit_code": task.exit_code,
        "stdout": task.stdout,
        "stderr": task.stderr
    }

    db.close()

    return task_data