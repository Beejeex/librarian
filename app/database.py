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
    """Create all SQLModel tables and apply lightweight schema migrations."""
    SQLModel.metadata.create_all(engine)
    _run_migrations()


def _run_migrations() -> None:
    """
    Add columns introduced after initial release.

    SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so we
    catch the OperationalError raised when a column already exists and
    continue — making this safe to run on every startup.
    """
    new_columns = [
        ("appconfig", "radarr_folder_format", "VARCHAR NOT NULL DEFAULT '{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}'"),
        ("appconfig", "sonarr_folder_format", "VARCHAR NOT NULL DEFAULT '{Series TitleYear} {tvdb-{TvdbId}}'"),
        ("renameitem", "disk_scenario", "VARCHAR NOT NULL DEFAULT 'unknown'"),
    ]
    with engine.connect() as conn:
        for table, column, definition in new_columns:
            try:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                conn.commit()
            except Exception:
                # Column already exists — safe to ignore
                pass


def get_session():
    """FastAPI dependency: yield a DB session, close it when done."""
    with Session(engine) as session:
        yield session
