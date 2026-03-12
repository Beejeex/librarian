"""
database.py — SQLite engine, session factory, and table creation.

Creates the SQLite database at /config/librarian.db (or in-memory for tests).
Provides a get_session() generator for FastAPI dependency injection.
"""

from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = "sqlite:////config/librarian.db"

# check_same_thread=False is required for SQLite when used with async frameworks
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables() -> None:
    """Create all SQLModel tables. Called once on app startup."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yield a DB session, close it when done."""
    with Session(engine) as session:
        yield session
