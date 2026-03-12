"""
ASGI entrypoint — mirrors the wsgi.py pattern used in the Flask Orders API.

Load .env BEFORE importing any app modules so that pydantic-settings
(and any module-level code that reads os.environ) sees the correct values.

Start with:
    uvicorn asgi:app --host 0.0.0.0 --port 8000 --workers 1 --log-level debug
Or via run.py (development):
    python run.py
"""
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env before importing any app code ────────────────────────────────
project_root = Path(__file__).resolve().parent
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=False)

# ── Now safe to import app modules ─────────────────────────────────────────
from app.main import app  # noqa: E402  (import after env load is intentional)

__all__ = ["app"]