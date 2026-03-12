"""
Development runner — mirrors the wsgi.py pattern used in the Flask Orders API.

Load .env BEFORE importing any app modules so that pydantic-settings
sees the correct values at module-import time.

Usage:
    python run.py
"""
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env before importing any app code ────────────────────────────────
project_root = Path(__file__).resolve().parent
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=False)

# ── Now safe to import app modules ─────────────────────────────────────────
import uvicorn                        # noqa: E402
from app.config import get_settings  # noqa: E402

settings = get_settings()

if __name__ == "__main__":
    uvicorn.run(
        "asgi:app",                   # points at asgi.py so .env is loaded first
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level="debug" if settings.app_debug else "info",
    )