"""
Shared HTTP client base for Arr application API calls (Radarr, Sonarr).

Provides authenticated GET requests and tag-name-to-ID resolution so
Radarr and Sonarr clients don't duplicate HTTP or auth logic.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ArrClient:
    """
    Thin async HTTP client for *arr APIs (Radarr, Sonarr).

    Handles authentication via X-Api-Key header and provides a shared
    get() helper that normalises error handling across both clients.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        """
        Args:
            base_url: Root URL of the Arr instance, e.g. "http://192.168.1.10:7878".
            api_key:  API key for the X-Api-Key header.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}

    async def get(self, path: str, params: dict | None = None) -> list[Any] | dict | None:
        """
        Perform an authenticated GET request and return parsed JSON.

        Returns None and logs an error on any network or non-2xx failure
        so callers can treat a failed request as an empty result rather
        than crashing the poll cycle.

        Args:
            path:   API path, e.g. "/api/v3/movie".
            params: Optional query parameters dict.
        """
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, headers=self._headers, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP %s from %s: %s", exc.response.status_code, url, exc.response.text)
        except httpx.RequestError as exc:
            logger.error("Request error reaching %s: %s", url, exc)
        return None

    async def resolve_tag_id(self, tag_name: str) -> int | None:
        """
        Resolve a tag name string to its numeric tag ID in the Arr app.

        Returns None if the tag doesn't exist or the API call fails.

        Args:
            tag_name: The tag label as shown in the Arr UI (case-insensitive).
        """
        tags = await self.get("/api/v3/tag")
        if not tags:
            return None
        # Match case-insensitively to be forgiving of UI input
        for tag in tags:
            if tag.get("label", "").lower() == tag_name.lower():
                return tag["id"]
        logger.warning("Tag '%s' not found in Arr instance at %s", tag_name, self._base_url)
        return None
