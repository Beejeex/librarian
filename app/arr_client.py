"""
arr_client.py — Base HTTP client for Radarr and Sonarr API communication.

Provides shared authentication (X-Api-Key header), timeout handling,
and GET/PUT helpers used by both RadarrClient and SonarrClient.

All arr API communication flows through this base — never use httpx directly
in other modules.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30


class BaseArrClient:
    """
    Shared HTTP client for arr applications (Radarr, Sonarr).

    Handles authentication and error logging. Subclasses add
    application-specific endpoints.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        # Never log the api_key — stored privately
        self._api_key = api_key
        self._headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        }

    async def get(self, path: str) -> dict | list:
        """
        Perform an authenticated GET request.
        Returns parsed JSON. Raises httpx.HTTPStatusError on non-2xx.
        """
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=self._headers)
        if not response.is_success:
            # Log URL and status but never the API key
            logger.error("GET %s returned %s", url, response.status_code)
            response.raise_for_status()
        return response.json()

    async def put(self, path: str, body: dict) -> dict:
        """
        Perform an authenticated PUT request with a JSON body.
        Returns parsed JSON. Raises httpx.HTTPStatusError on non-2xx.
        """
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.put(url, headers=self._headers, json=body)
        if not response.is_success:
            logger.error("PUT %s returned %s", url, response.status_code)
            response.raise_for_status()
        return response.json()
