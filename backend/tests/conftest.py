from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["USE_CELERY"] = "false"
os.environ["DEMO_MODE"] = "true"
os.environ["OBSERVATION_SECONDS"] = "0"
os.environ["PROBE_INTERVAL_SECONDS"] = "0"
os.environ["WEBHOOK_SECRET"] = "dev-webhook-secret"
os.environ["OPENAI_API_KEY"] = ""
os.environ["PLAYBOOKS_DIR"] = str(ROOT / "playbooks")
os.environ["KNOWLEDGE_DIR"] = str(ROOT / "knowledge")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app import database as dbmod  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_db():
    get_settings.cache_clear()
    dbmod.Base.metadata.drop_all(bind=dbmod.engine)
    dbmod.Base.metadata.create_all(bind=dbmod.engine)
    from app.seed import seed_if_empty

    db = dbmod.SessionLocal()
    try:
        seed_if_empty(db)
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def client(_reset_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(_reset_db) -> Session:
    session = dbmod.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def auth_headers(secret: str = "dev-webhook-secret") -> dict[str, str]:
    return {"X-Webhook-Secret": secret}
