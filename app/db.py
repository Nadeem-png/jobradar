"""Database engine, session factory, and declarative base.

Backend is chosen from the environment (loaded from .env by app/__init__.py):

  1. DATABASE_URL           -> used verbatim (any SQLAlchemy URL)
  2. MYSQL_HOST (+ friends) -> MySQL via PyMySQL
  3. otherwise              -> local SQLite file (jobradar.db), for dev/tests

MySQL vars: MYSQL_HOST, MYSQL_PORT (3306), MYSQL_USER (root), MYSQL_PASSWORD,
MYSQL_DB (jobradar).
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

log = logging.getLogger("jobradar.db")


def _build_engine_config() -> tuple[str, dict]:
    """Return (database_url, create_engine_kwargs) based on the environment."""
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        kwargs: dict = {"pool_pre_ping": True}
        if explicit.startswith("mysql"):
            kwargs["pool_recycle"] = 3600
        elif explicit.startswith("sqlite"):
            kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        return explicit, kwargs

    if os.getenv("MYSQL_HOST"):
        user = os.getenv("MYSQL_USER", "root")
        pw = os.getenv("MYSQL_PASSWORD", "")
        host = os.getenv("MYSQL_HOST", "127.0.0.1")
        port = os.getenv("MYSQL_PORT", "3306")
        db = os.getenv("MYSQL_DB", "jobradar")
        url = (
            f"mysql+pymysql://{quote_plus(user)}:{quote_plus(pw)}@{host}:{port}/{db}"
            "?charset=utf8mb4"
        )
        # pool_pre_ping avoids "MySQL server has gone away" on idle connections;
        # pool_recycle keeps connections under MySQL's wait_timeout.
        return url, {"pool_pre_ping": True, "pool_recycle": 3600}

    # SQLite fallback.
    env_path = os.getenv("JOBRADAR_DB_PATH")
    if env_path:
        db_path = Path(env_path)
    elif os.getenv("VERCEL") == "1" or os.getenv("JOBRADAR_SERVERLESS") == "1":
        # Serverless (Vercel): the app bundle is read-only; only the temp dir is
        # writable. Ephemeral by design — data resets on cold starts, and the
        # feed is refilled via /cron/fetch or "Fetch now" (documented).
        db_path = Path(tempfile.gettempdir()) / "jobradar.db"
    else:
        db_path = Path(__file__).resolve().parent.parent / "jobradar.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}", {
        "connect_args": {"check_same_thread": False, "timeout": 30}
    }


DATABASE_URL, _ENGINE_KWARGS = _build_engine_config()

engine = create_engine(DATABASE_URL, echo=False, **_ENGINE_KWARGS)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def is_mysql() -> bool:
    return engine.dialect.name == "mysql"


def ensure_database_exists() -> None:
    """For MySQL: create the target database if it doesn't exist yet.

    Connects to the server (no database selected) via PyMySQL and issues
    CREATE DATABASE IF NOT EXISTS, so a fresh MySQL install works out of the box
    (as long as the user has CREATE privilege). No-op for SQLite.
    """
    if not is_mysql():
        return
    import pymysql

    url = make_url(DATABASE_URL)
    dbname = url.database
    conn = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
        log.info("ensured MySQL database `%s` exists", dbname)
    finally:
        conn.close()


def get_db():
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
