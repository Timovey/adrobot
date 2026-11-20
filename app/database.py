import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger("adrobot.db")


class Base(DeclarativeBase):
    pass


def _is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def build_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if _is_sqlite(database_url) else {}
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


settings = get_settings()
engine = build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_sqlite_dir(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return
    database = parsed.database
    if database and database != ":memory:" and not database.startswith("file:"):
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def _migrate(bind: Engine) -> None:
    """Tiny in-place migrations for existing databases (no Alembic)."""
    insp = inspect(bind)
    if "streams" in insp.get_table_names():
        columns = {c["name"] for c in insp.get_columns("streams")}
        if "schema" in columns and "stream_schema" not in columns:
            logger.info("Migrating streams.schema -> streams.stream_schema")
            with bind.begin() as conn:
                conn.execute(text('ALTER TABLE streams RENAME COLUMN "schema" TO stream_schema'))


def init_db(bind: Engine | None = None) -> None:
    from app import models  # noqa: F401

    bind = bind or engine
    _ensure_sqlite_dir(str(bind.url))
    _migrate(bind)
    Base.metadata.create_all(bind=bind)
