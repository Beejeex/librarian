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
        # Renamer columns (added post v0.0.2)
        ("appconfig", "radarr_folder_format", "VARCHAR NOT NULL DEFAULT '{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}'"),
        ("appconfig", "sonarr_folder_format", "VARCHAR NOT NULL DEFAULT '{Series TitleYear} {tvdb-{TvdbId}}'"),
        ("renameitem", "disk_scenario", "VARCHAR NOT NULL DEFAULT 'unknown'"),
        # Tracker: AppConfig fields (added in v0.1.0)
        ("appconfig", "radarr_tags", "VARCHAR NOT NULL DEFAULT ''"),
        ("appconfig", "sonarr_tags", "VARCHAR NOT NULL DEFAULT ''"),
        ("appconfig", "poll_interval_minutes", "INTEGER NOT NULL DEFAULT 15"),
        ("appconfig", "share_path", "VARCHAR NOT NULL DEFAULT '/share'"),
        ("appconfig", "copy_mode", "VARCHAR NOT NULL DEFAULT 'copy'"),
        ("appconfig", "radarr_first_run_complete", "BOOLEAN NOT NULL DEFAULT 0"),
        ("appconfig", "sonarr_first_run_complete", "BOOLEAN NOT NULL DEFAULT 0"),
        ("appconfig", "require_approval", "BOOLEAN NOT NULL DEFAULT 0"),
        ("appconfig", "max_concurrent_copies", "INTEGER NOT NULL DEFAULT 2"),
        ("appconfig", "max_share_size_gb", "REAL NOT NULL DEFAULT 0.0"),
        ("appconfig", "max_share_files", "INTEGER NOT NULL DEFAULT 0"),
        ("appconfig", "ntfy_url", "VARCHAR NOT NULL DEFAULT 'https://ntfy.sh'"),
        ("appconfig", "ntfy_topic", "VARCHAR NOT NULL DEFAULT ''"),
        ("appconfig", "ntfy_token", "VARCHAR NOT NULL DEFAULT ''"),
        ("appconfig", "ntfy_on_copied", "BOOLEAN NOT NULL DEFAULT 1"),
        ("appconfig", "ntfy_on_error", "BOOLEAN NOT NULL DEFAULT 1"),
        ("appconfig", "ntfy_on_finished", "BOOLEAN NOT NULL DEFAULT 1"),
        ("appconfig", "ntfy_on_first_run", "BOOLEAN NOT NULL DEFAULT 1"),
        # Tracker: TrackedItem fields (added in v0.1.0)
        ("trackeditem", "is_upgraded", "BOOLEAN NOT NULL DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for table, column, definition in new_columns:
            try:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                conn.commit()
            except Exception:
                # Column already exists — safe to ignore
                pass


def get_session() -> Session:
    """
    Return a new SQLModel Session bound to the shared engine.

    Always use as a context manager:
        with get_session() as session:
            ...
    """
    return Session(engine)


def get_session_dep():
    """FastAPI dependency: yield a DB session, close it after the response."""
    with Session(engine) as session:
        yield session
