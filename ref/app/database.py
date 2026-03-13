"""
Database engine and session management for MadTracked.

Creates the SQLite engine pointed at /config/madtracked.db and exposes
a session factory used by all DB operations. The engine is module-level
so it is shared across the entire process lifetime.
"""

import logging

from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

# SQLite file lives in the /config volume so it survives container restarts
DATABASE_URL = "sqlite:////config/madtracked.db"

# check_same_thread=False is required for SQLite when used with FastAPI's async request threads
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables() -> None:
    """Create all SQLModel tables if they don't already exist."""
    SQLModel.metadata.create_all(engine)


def run_migrations() -> None:
    """Apply any schema migrations needed for columns added after initial release."""
    with engine.connect() as conn:
        # Fetch existing columns for trackeditem
        result = conn.execute(__import__("sqlalchemy").text("PRAGMA table_info(trackeditem)"))
        existing_columns = {row[1] for row in result}

        if "is_upgraded" not in existing_columns:
            conn.execute(__import__("sqlalchemy").text(
                "ALTER TABLE trackeditem ADD COLUMN is_upgraded BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
            logger.info("Migration applied: added is_upgraded column to trackeditem")


def get_session() -> Session:
    """
    Return a new SQLModel Session bound to the shared engine.

    Always use as a context manager:
        with get_session() as session:
            ...
    """
    return Session(engine)
