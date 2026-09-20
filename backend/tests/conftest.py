from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["USE_CELERY"] = "false"
os.environ["DEMO_MODE"] = "true"
os.environ["OBSERVATION_SECONDS"] = "0"
os.environ["PROBE_INTERVAL_SECONDS"] = "0"
os.environ["WEBHOOK_SECRET"] = "dev-webhook-secret"
os.environ["INTEGRATION_MODE"] = "mock"
# 压平开发者本地 .env 中的真实集成凭据，保证测试确定性（env 变量优先于 dotenv）
# 注意：不要设置 ZABBIX_MODE/ANSIBLE_MODE/VAULT_MODE，其优先级高于 INTEGRATION_MODE，会影响工厂用例
os.environ["ZABBIX_URL"] = ""
os.environ["ZABBIX_USER"] = ""
os.environ["ZABBIX_PASSWORD"] = ""
os.environ["ZABBIX_TOKEN"] = ""
os.environ["VAULT_ADDR"] = ""
os.environ["VAULT_TOKEN"] = ""
os.environ["STRESS_TOOLS_ENABLED"] = "false"
os.environ["ACTION_FAIL_COOLDOWN_SECONDS"] = "1800"
os.environ["PLAYBOOKS_DIR"] = str(ROOT / "playbooks")
os.environ["KNOWLEDGE_DIR"] = str(ROOT / "knowledge")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings  # noqa: E402

# 测试一律只认环境变量，禁止读取 CWD 下开发者的 .env（避免真实集成配置渗入）
Settings.model_config["env_file"] = None

get_settings.cache_clear()

from app import database as dbmod  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_db():
    get_settings.cache_clear()
    from app.integrations import clear_integration_probe_cache

    clear_integration_probe_cache()
    from app.domain.catalog import load_catalog

    load_catalog.cache_clear()
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


@pytest.fixture
def auth_token(_reset_db) -> str:
    """以 admin 身份签发真实 JWT（含 jti 白名单），走生产同款代码路径。"""
    from app.models import User, UserToken
    from app.security import create_access_token

    db = dbmod.SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert user is not None, "seed 未创建 admin 用户"
        token, jti, expires_at = create_access_token(user.id, ip="testclient", user_agent="pytest")
        db.add(UserToken(jti=jti, user_id=user.id, expires_at=expires_at, ip="testclient"))
        db.commit()
        return token
    finally:
        db.close()


@pytest.fixture
def client(_reset_db, auth_token):
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {auth_token}"})
        yield c


def auth_headers(secret: str = "dev-webhook-secret") -> dict[str, str]:
    return {"X-Webhook-Secret": secret}
