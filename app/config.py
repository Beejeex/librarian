"""
config.py — Application configuration loader.

Configuration has two layers:
  1. Environment variables: seed the AppConfig row on first startup.
  2. DB row (AppConfig id=1): authoritative source at runtime.

The Settings UI writes to the DB row. All runtime code reads from the DB row,
never directly from environment variables.
"""

import os
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models import AppConfig


def _seed_from_env() -> dict:
    """Read environment variables and return a dict of config values."""
    return {
        "radarr_url": os.getenv("RADARR_URL", ""),
        "radarr_api_key": os.getenv("RADARR_API_KEY", ""),
        "radarr_root_folder": os.getenv("RADARR_ROOT_FOLDER", "/movies"),
        "sonarr_url": os.getenv("SONARR_URL", ""),
        "sonarr_api_key": os.getenv("SONARR_API_KEY", ""),
        "sonarr_root_folder": os.getenv("SONARR_ROOT_FOLDER", "/tv"),
        "batch_size": int(os.getenv("BATCH_SIZE", "20")),
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


def save_config(session: Session, data: dict) -> AppConfig:
    """
    Update the AppConfig row with the provided values.
    Creates the row if it doesn't exist.
    """
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


def get_radarr_client(config: AppConfig):
    """Instantiate a RadarrClient from the current config."""
    from app.radarr import RadarrClient
    return RadarrClient(config.radarr_url, config.radarr_api_key)


def get_sonarr_client(config: AppConfig):
    """Instantiate a SonarrClient from the current config."""
    from app.sonarr import SonarrClient
    return SonarrClient(config.sonarr_url, config.sonarr_api_key)
