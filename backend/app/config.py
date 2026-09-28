"""Environment-based configuration. All default paths are relative to the repository."""
from dataclasses import dataclass
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    scripts_dir: Path = PROJECT_ROOT / "scripts"
    database_path: Path = PROJECT_ROOT / "automation.db"
    api_token: str = ""
    max_workers: int = 2
    max_pending: int = 20
    timeout_seconds: int = 300
    max_output_bytes: int = 1_000_000
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")

    def __post_init__(self):
        for name in ("max_workers", "max_pending", "timeout_seconds", "max_output_bytes"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must not be empty")

    @classmethod
    def from_env(cls):
        return cls(
            scripts_dir=Path(os.getenv("PAS_SCRIPTS_DIR", str(PROJECT_ROOT / "scripts"))).resolve(),
            database_path=Path(os.getenv("PAS_DATABASE_PATH", str(PROJECT_ROOT / "automation.db"))).resolve(),
            api_token=os.getenv("PAS_API_TOKEN", ""),
            max_workers=int(os.getenv("PAS_MAX_WORKERS", "2")),
            max_pending=int(os.getenv("PAS_MAX_PENDING", "20")),
            timeout_seconds=int(os.getenv("PAS_TIMEOUT_SECONDS", "300")),
            max_output_bytes=int(os.getenv("PAS_MAX_OUTPUT_BYTES", "1000000")),
            allowed_hosts=tuple(h.strip() for h in os.getenv(
                "PAS_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"
            ).split(",") if h.strip()),
        )
