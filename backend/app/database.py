from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import DateTime, TypeDecorator

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator):
    """带时区语义的 DateTime。

    MySQL 的 DATETIME 不存时区：写入时统一转换为 UTC 去掉 tzinfo，
    读取时统一补回 UTC tzinfo。这样 Python 侧始终保持 aware datetime，
    比较运算与 JSON 序列化（+00:00）行为与 PostgreSQL 一致。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _create_engine_for(url: str) -> Engine:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        # 内存库必须全线程共享同一连接，否则测试里建表后应用线程看到的是空库
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    elif url.startswith("mysql"):
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=1800, pool_timeout=30)
    return create_engine(url, **kwargs)


settings = get_settings()
engine = _create_engine_for(settings.database_url)
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


def run_migrations(url: str | None = None) -> None:
    """执行 Alembic 迁移到最新版本（生产 MySQL 启动时调用）。

    sqlite 仅用于本地/测试（create_all 建表），不走迁移。
    """
    target = url or get_settings().database_url
    if target.startswith("sqlite"):
        return
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]  # backend/
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", target)
    command.upgrade(cfg, "head")


def init_engine(url: str | None = None):
    """Rebind engine (used by tests)."""
    global engine, SessionLocal
    target = url or get_settings().database_url
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if target.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(target, **kwargs)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return engine
