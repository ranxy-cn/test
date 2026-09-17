from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    return kwargs


settings = get_settings()
engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ensure_schema() -> None:
    """create_all + 增量列（已有 docker volume 不会自动 ALTER）。"""
    Base.metadata.create_all(bind=engine)
    try:
        insp = inspect(engine)
        if "assets" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("assets")}
    except Exception:
        return
    stmts = []
    if "external_id" not in cols:
        stmts.append("ALTER TABLE assets ADD COLUMN external_id VARCHAR(64) DEFAULT ''")
    if "zabbix_host" not in cols:
        stmts.append("ALTER TABLE assets ADD COLUMN zabbix_host VARCHAR(128) DEFAULT ''")
    if not stmts:
        return
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


def init_engine(url: str | None = None):
    """Rebind engine (used by tests)."""
    global engine, SessionLocal
    target = url or get_settings().database_url
    engine = create_engine(target, **_engine_kwargs(target))
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return engine
