"""
config.py — Application configuration loader.

Configuration has two layers:
  1. Environment variables: seed the AppConfig row on first startup.
  2. DB row (AppConfig id=1): authoritative source at runtime.

The Settings UI writes to the DB row. All runtime code reads from the DB row,
never directly from environment variables.
"""

import logging
import os
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.database import get_session
from app.models import AppConfig

logger = logging.getLogger(__name__)


def _seed_from_env() -> dict:
    """Read environment variables and return a dict of config values."""
    return {
        "radarr_url": os.getenv("RADARR_URL", ""),
        "radarr_api_key": os.getenv("RADARR_API_KEY", ""),
        "radarr_root_folder": os.getenv("RADARR_ROOT_FOLDER", "/movies"),
        "radarr_tags": os.getenv("RADARR_TAGS", ""),
        "sonarr_url": os.getenv("SONARR_URL", ""),
        "sonarr_api_key": os.getenv("SONARR_API_KEY", ""),
        "sonarr_root_folder": os.getenv("SONARR_ROOT_FOLDER", "/tv"),
        "sonarr_tags": os.getenv("SONARR_TAGS", ""),
        "batch_size": int(os.getenv("BATCH_SIZE", "20")),
        "poll_interval_minutes": int(os.getenv("POLL_INTERVAL_MINUTES", "15")),
        "share_path": os.getenv("SHARE_PATH", "/share"),
        "copy_mode": "copy",
        "radarr_first_run_complete": False,
        "sonarr_first_run_complete": False,
        "require_approval": os.getenv("REQUIRE_APPROVAL", "false").lower() == "true",
        "max_concurrent_copies": int(os.getenv("MAX_CONCURRENT_COPIES", "2")),
        "max_share_size_gb": float(os.getenv("MAX_SHARE_SIZE_GB", "0")),
        "max_share_files": int(os.getenv("MAX_SHARE_FILES", "0")),
        "ntfy_url": os.getenv("NTFY_URL", "https://ntfy.sh"),
        "ntfy_topic": os.getenv("NTFY_TOPIC", ""),
        "ntfy_token": os.getenv("NTFY_TOKEN", ""),
        "ntfy_on_copied": os.getenv("NTFY_ON_COPIED", "true").lower() == "true",
        "ntfy_on_error": os.getenv("NTFY_ON_ERROR", "true").lower() == "true",
        "ntfy_on_finished": os.getenv("NTFY_ON_FINISHED", "true").lower() == "true",
        "ntfy_on_first_run": os.getenv("NTFY_ON_FIRST_RUN", "true").lower() == "true",
    }


def get_config(session: Session) -> AppConfig:
    """
    Return the AppConfig row (id=1).
    If it doesn't exist yet, create it from environment variables and save.
    """
    config = session.get(AppConfig, 1)
    if config is None:
        config = AppConfig(**_seed_from_env())
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def load_config() -> AppConfig:
    """
    Return the current AppConfig row from the DB (no session arg).
    If no row exists yet, create one seeded from environment variables.
    Used by the scheduler and watcher which manage their own sessions.
    """
    with get_session() as session:
        config = session.exec(select(AppConfig)).first()
        if config is None:
            config = AppConfig(**_seed_from_env())
            session.add(config)
            session.commit()
            session.refresh(config)
            logger.info("AppConfig initialized from environment variables.")
        return config


def save_config(session_or_config, data: dict | None = None) -> AppConfig:
    """
    Update the AppConfig row.

    Accepts two call patterns:
      save_config(session, data_dict)   — used by UI routers
      save_config(app_config_obj)       — used by scheduler / watcher
    """
    if isinstance(session_or_config, AppConfig):
        # Scheduler/watcher pattern: save_config(config_obj)
        updated = session_or_config
        with get_session() as session:
            updated.id = 1  # Enforce single-row constraint
            session.merge(updated)
            session.commit()
            refreshed = session.exec(select(AppConfig)).first()
            logger.info("AppConfig updated.")
            return refreshed
    else:
        # Router pattern: save_config(session, data_dict)
        session = session_or_config
        config = session.get(AppConfig, 1)
        if config is None:
            config = AppConfig(**data)
        else:
            for key, value in data.items():
                setattr(config, key, value)
            config.updated_at = datetime.now(UTC)
        session.add(config)
        session.commit()
        session.refresh(config)
        return config


def mask_secrets(config: AppConfig) -> dict:
    """Return a dict of config values with sensitive fields redacted for safe logging."""
    return {
        "radarr_url": config.radarr_url,
        "radarr_api_key": "***" if config.radarr_api_key else "",
        "radarr_tags": config.radarr_tags,
        "sonarr_url": config.sonarr_url,
        "sonarr_api_key": "***" if config.sonarr_api_key else "",
        "sonarr_tags": config.sonarr_tags,
        "poll_interval_minutes": config.poll_interval_minutes,
        "share_path": config.share_path,
        "copy_mode": config.copy_mode,
        "radarr_first_run_complete": config.radarr_first_run_complete,
        "sonarr_first_run_complete": config.sonarr_first_run_complete,
        "require_approval": config.require_approval,
        "max_concurrent_copies": config.max_concurrent_copies,
        "max_share_size_gb": config.max_share_size_gb,
        "max_share_files": config.max_share_files,
        "ntfy_url": config.ntfy_url,
        "ntfy_topic": config.ntfy_topic,
        "ntfy_token": "***" if config.ntfy_token else "",
    }


def get_radarr_client(config: AppConfig):
    """Instantiate a RadarrClient from the current config."""
    from app.radarr import RadarrClient
    return RadarrClient(config.radarr_url, config.radarr_api_key)


def get_sonarr_client(config: AppConfig):
    """Instantiate a SonarrClient from the current config."""
    from app.sonarr import SonarrClient
    return SonarrClient(config.sonarr_url, config.sonarr_api_key)

