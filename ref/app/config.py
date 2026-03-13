"""
Configuration loader for MadTracked.

On startup, environment variables seed the AppConfig row in SQLite if no row
exists yet. At runtime, the DB row is the authoritative source — the UI can
update it without restarting the container.

API keys are never logged; use mask_secrets() before printing any config values.
"""

import os
import logging
from typing import Optional

from sqlmodel import select

from app.database import get_session
from app.models import AppConfig

logger = logging.getLogger(__name__)


def load_config() -> AppConfig:
    """
    Return the current AppConfig row from the DB.

    If no row exists yet, create one seeded from environment variables.
    """
    with get_session() as session:
        config = session.exec(select(AppConfig)).first()
        if config is None:
            config = _create_default_config()
            session.add(config)
            session.commit()
            session.refresh(config)
            logger.info("AppConfig initialized from environment variables.")
        return config


def save_config(updated: AppConfig) -> AppConfig:
    """
    Persist an updated AppConfig row to the DB and return the refreshed instance.

    Always operates on row ID=1 to enforce the single-row contract.
    """
    with get_session() as session:
        updated.id = 1  # Enforce single-row constraint
        session.merge(updated)
        session.commit()
        config = session.exec(select(AppConfig)).first()
        logger.info("AppConfig updated.")
        return config


def _create_default_config() -> AppConfig:
    """Build an AppConfig populated from env vars, falling back to safe defaults."""
    return AppConfig(
        radarr_url=os.getenv("RADARR_URL", ""),
        radarr_api_key=os.getenv("RADARR_API_KEY", ""),
        radarr_tags=os.getenv("RADARR_TAGS", ""),
        sonarr_url=os.getenv("SONARR_URL", ""),
        sonarr_api_key=os.getenv("SONARR_API_KEY", ""),
        sonarr_tags=os.getenv("SONARR_TAGS", ""),
        poll_interval_minutes=int(os.getenv("POLL_INTERVAL_MINUTES", "15")),
        share_path="/share",
        copy_mode="copy",
        radarr_first_run_complete=False,  # Always start fresh; never read from env
        sonarr_first_run_complete=False,
        require_approval=os.getenv("REQUIRE_APPROVAL", "false").lower() == "true",
        max_concurrent_copies=int(os.getenv("MAX_CONCURRENT_COPIES", "2")),
        max_share_size_gb=float(os.getenv("MAX_SHARE_SIZE_GB", "0")),
        max_share_files=int(os.getenv("MAX_SHARE_FILES", "0")),
        radarr_root_folder=os.getenv("RADARR_ROOT_FOLDER", "/movies"),
        sonarr_root_folder=os.getenv("SONARR_ROOT_FOLDER", "/tv"),
        ntfy_url=os.getenv("NTFY_URL", "https://ntfy.sh"),
        ntfy_topic=os.getenv("NTFY_TOPIC", ""),
        ntfy_token=os.getenv("NTFY_TOKEN", ""),
        ntfy_on_copied=os.getenv("NTFY_ON_COPIED", "true").lower() == "true",
        ntfy_on_error=os.getenv("NTFY_ON_ERROR", "true").lower() == "true",
        ntfy_on_finished=os.getenv("NTFY_ON_FINISHED", "true").lower() == "true",
        ntfy_on_first_run=os.getenv("NTFY_ON_FIRST_RUN", "true").lower() == "true",
    )


def mask_secrets(config: AppConfig) -> dict:
    """Return a dict of config values with API keys replaced by '***' for safe logging."""
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
        "radarr_root_folder": config.radarr_root_folder,
        "sonarr_root_folder": config.sonarr_root_folder,
        "ntfy_url": config.ntfy_url,
        "ntfy_topic": config.ntfy_topic,
        "ntfy_token": "***" if config.ntfy_token else "",
    }
