"""Environment-driven settings. No secrets live in source code."""
import os

_db = os.getenv("DATABASE_URL", "sqlite:///./data/patients.db")
# Heroku/Render style URLs use postgres://, SQLAlchemy wants postgresql://
DATABASE_URL = _db.replace("postgres://", "postgresql://", 1) if _db.startswith("postgres://") else _db
VAPI_SECRET = os.getenv("VAPI_SECRET", "")
SEED_DEMO_DATA = os.getenv("SEED_DEMO_DATA", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
