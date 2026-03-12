# TODO 02 — Database Models & Config

## Goal
Implement `database.py`, `models.py`, and `config.py`. The app must be able to read configuration from environment variables, persist it in SQLite, and have a working DB session factory.

---

## Tasks

### 2.1 — app/database.py
- Create SQLite engine pointed at `/config/librarian.db`.
- Provide a `get_session()` generator for FastAPI `Depends()` injection.
- Provide `create_db_and_tables()` called from the lifespan startup.
- Engine must use `check_same_thread=False` (required for SQLite with async).

### 2.2 — app/models.py

Implement three SQLModel table classes:

#### `AppConfig`
```python
class AppConfig(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_root_folder: str = "/movies"
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    sonarr_root_folder: str = "/tv"
    batch_size: int = 20
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

#### `ScanRun`
```python
class ScanRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str               # "radarr" or "sonarr"
    status: str               # "scanning", "ready", "applying", "done", "error"
    total_items: int = 0
    done_count: int = 0
    error_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

#### `RenameItem`
```python
class RenameItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scan_run_id: int = Field(foreign_key="scanrun.id")
    source: str               # "radarr" or "sonarr"
    source_id: int            # arr's internal ID
    title: str
    current_folder: str
    expected_folder: str
    current_path: str         # full path in arr namespace
    expected_path: str        # full expected path in arr namespace
    status: str = "pending"   # pending, approved, skipped, done, error
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

### 2.3 — app/config.py

Implement `get_config(session)` and `save_config(session, data)`:
- `get_config`: fetch the single `AppConfig` row (id=1). If it doesn't exist, create it from environment variables and save.
- `save_config`: update the row with new values, set `updated_at = now()`.
- On startup (lifespan), call `get_config` once to ensure the row exists.

#### Environment variable seeding (first startup only)
```python
import os

def seed_from_env() -> dict:
    return {
        "radarr_url": os.getenv("RADARR_URL", ""),
        "radarr_api_key": os.getenv("RADARR_API_KEY", ""),
        "radarr_root_folder": os.getenv("RADARR_ROOT_FOLDER", "/movies"),
        "sonarr_url": os.getenv("SONARR_URL", ""),
        "sonarr_api_key": os.getenv("SONARR_API_KEY", ""),
        "sonarr_root_folder": os.getenv("SONARR_ROOT_FOLDER", "/tv"),
        "batch_size": int(os.getenv("BATCH_SIZE", "20")),
    }
```

---

## Acceptance Criteria
- [ ] `AppConfig`, `ScanRun`, `RenameItem` tables created in SQLite on startup
- [ ] `AppConfig` row (id=1) created automatically if it doesn't exist
- [ ] Env vars populate the config row on first run
- [ ] Settings saved via `save_config` persist across container restarts (via `/config` volume)
- [ ] `get_session()` works as a FastAPI dependency
