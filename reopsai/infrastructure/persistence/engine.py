import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


_engine = None
_SessionLocal = None


def _resolve_database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def init_engine(validate_connection: bool = False):
    """
    Initialize SQLAlchemy engine/sessionmaker once.
    Returns True when initialized, False when DATABASE_URL is missing.
    """
    global _engine, _SessionLocal

    if _engine is not None:
        return True

    database_url = _resolve_database_url()
    if not database_url:
        return False

    _engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
    )
    _SessionLocal = sessionmaker(
        bind=_engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )

    if validate_connection:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    return True


def get_engine():
    if _engine is None:
        raise RuntimeError("SQLAlchemy engine is not initialized. Call init_engine() first.")
    return _engine


def get_session_factory():
    if _SessionLocal is None:
        raise RuntimeError("Session factory is not initialized. Call init_engine() first.")
    return _SessionLocal


@contextmanager
def session_scope():
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
