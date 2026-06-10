"""HTTP client for the HydroVu public API.

Authenticates with OAuth2 client credentials and follows the
``X-ISI-Start-Page`` header convention for pagination. Returns raw
(untransformed) JSON payloads — mapping to the canonical model is the
adapter's job, not the client's.
"""

from __future__ import annotations

import time
from typing import Any

import httpx


class HydroVuAuthError(RuntimeError):
    """Raised when the OAuth token request fails."""


class HydroVuApiError(RuntimeError):
    """Raised when an authenticated API request fails."""


class HydroVuClient:
    """Authenticated client for the HydroVu public API.

    Usage::

        with HydroVuClient(client_id, client_secret) as client:
            locations = client.list_locations()
    """

    PAGE_HEADER = "X-ISI-Start-Page"
    TOKEN_EXPIRY_MARGIN_SECONDS = 60.0

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str = "https://www.hydrovu.com/public-api/oauth/token",
        api_base_url: str = "https://www.hydrovu.com/public-api/v1",
        timeout: float = 30.0,
    ) -> None:
        """Store credentials and endpoints; no network calls happen here."""
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._api_base_url = api_base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def list_locations(self) -> list[dict[str, Any]]:
        """Return all locations for the account as one flat list."""
        pages = self._get_paginated("/locations/list")
        locations: list[dict[str, Any]] = []
        for page in pages:
            locations.extend(page)
        return locations

    def get_location_data(
        self, location_id: int, start_time: int, end_time: int
    ) -> list[dict[str, Any]]:
        """Return raw readings pages for one location over a UTC epoch window.

        Pages are returned verbatim (unmerged) so the staged objects preserve
        the API payloads exactly.
        """
        return self._get_paginated(
            f"/locations/{location_id}/data",
            params={"startTime": str(start_time), "endTime": str(end_time)},
        )

    def get_friendly_names(self) -> dict[str, Any]:
        """Return the parameter/unit friendly-name mappings."""
        response = self._get("/sispec/friendlynames")
        payload: dict[str, Any] = response.json()
        return payload

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> HydroVuClient:
        """Return self for use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the client on context exit."""
        self.close()

    def _get_token(self) -> str:
        """Return a cached bearer token, fetching a new one when stale."""
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        response = self._http.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if response.status_code != 200:
            raise HydroVuAuthError(
                f"token request failed: {response.status_code} {response.text}"
            )
        payload = response.json()
        self._token = str(payload["access_token"])
        expires_in = float(payload.get("expires_in", 3600))
        self._token_expires_at = (
            time.monotonic() + expires_in - self.TOKEN_EXPIRY_MARGIN_SECONDS
        )
        return self._token

    def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue one authenticated GET and raise HydroVuApiError on failure."""
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        if extra_headers:
            headers.update(extra_headers)
        response = self._http.get(
            f"{self._api_base_url}{path}", params=params, headers=headers
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HydroVuApiError(
                f"GET {path} failed: {response.status_code} {response.text}"
            ) from exc
        return response

    def _get_paginated(
        self, path: str, params: dict[str, str] | None = None
    ) -> list[Any]:
        """GET all pages of a paginated endpoint, one list entry per page.

        HydroVu signals more data by returning an ``X-ISI-Start-Page`` response
        header; the value is echoed back as a request header to fetch the next
        page.
        """
        pages: list[Any] = []
        next_page: str | None = None
        while True:
            extra_headers = {self.PAGE_HEADER: next_page} if next_page else None
            response = self._get(path, params=params, extra_headers=extra_headers)
            pages.append(response.json())
            next_page = response.headers.get(self.PAGE_HEADER)
            if not next_page:
                return pages
