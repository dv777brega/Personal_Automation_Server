from fastapi import FastAPI
from backend.app.services.task_service import run_script
from backend.app.database import engine
from backend.app.models.task import Base

Base.metadata.create_all(bind=engine)
app = FastAPI()

@app.get("/api/health")
def health_check():
    return {"status": "ok!"}

@app.post("/api/scripts/{script_name}/run")
def run_script_endpoint(script_name: str):
    return run_script(script_name)