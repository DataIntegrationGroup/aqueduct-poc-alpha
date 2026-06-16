"""Shared fixtures for the aqueduct-poc-alpha test suite."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from aqueduct_cloud_functions.clients import HydroVuApiError


class FakeHydroVuClient:
    """In-memory stand-in for HydroVuClient (no network)."""

    def __init__(
        self,
        locations: list[dict[str, Any]],
        data_page: dict[str, Any],
        failing_location_ids: set[int] | None = None,
        no_data_location_ids: set[int] | None = None,
    ) -> None:
        """Serve canned pages; raise for failing ids, return [] for no-data ids."""
        self._locations = locations
        self._data_page = data_page
        self._failing = failing_location_ids or set()
        self._no_data = no_data_location_ids or set()

    def list_locations(self) -> list[dict[str, Any]]:
        """Return the canned location list."""
        return self._locations

    def get_friendly_names(self) -> dict[str, Any]:
        """Return a canned friendly-names payload."""
        return {"parameters": {"4": "Depth to Water"}, "units": {"35": "ft"}}

    def get_location_data(
        self, location_id: int, start_time: int, end_time: int
    ) -> list[dict[str, Any]]:
        """Return one canned page; raise on failing ids; [] on no-data ids.

        Mirrors the real client, which returns ``[]`` for a 404 "no data"
        response and raises ``HydroVuApiError`` (with ``status_code``) otherwise.
        """
        if location_id in self._failing:
            raise HydroVuApiError(
                f"GET /locations/{location_id}/data failed: 500", status_code=500
            )
        if location_id in self._no_data:
            return []
        return [self._data_page]

    def close(self) -> None:
        """No-op for interface parity with the real client."""


class FakeGcsClient:
    """Records written objects instead of touching GCS."""

    def __init__(self) -> None:
        """Start with an empty object store."""
        self.objects: dict[str, Any] = {}

    def write_json(self, object_path: str, payload: Any) -> str:
        """Record the payload and return a fake gs:// URI."""
        self.objects[object_path] = payload
        return f"gs://test-bucket/{object_path}"


@pytest.fixture
def fake_gcs() -> FakeGcsClient:
    """A fresh recording GCS client."""
    return FakeGcsClient()


@pytest.fixture
def make_fake_hydrovu(
    sample_locations: list[dict[str, Any]], sample_data_page: dict[str, Any]
) -> Callable[..., FakeHydroVuClient]:
    """Return a factory for FakeHydroVuClient seeded with the sample data."""

    def _make(
        failing_location_ids: set[int] | None = None,
        no_data_location_ids: set[int] | None = None,
    ) -> FakeHydroVuClient:
        """Build a fake client, optionally failing or no-data'ing location ids."""
        return FakeHydroVuClient(
            sample_locations,
            sample_data_page,
            failing_location_ids,
            no_data_location_ids,
        )

    return _make


@pytest.fixture
def ingest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env vars PvacdIngestSettings requires."""
    monkeypatch.setenv("HYDROVU_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("HYDROVU_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")


@pytest.fixture
def sample_locations() -> list[dict[str, Any]]:
    """HydroVu location records, shaped like bravo's adapter fixtures."""
    return [
        {
            "id": 123,
            "name": "Zumwalt Well",
            "gps": {"latitude": 36.1, "longitude": -106.2, "elevation": 5400.0},
        },
        {
            "id": 456,
            "name": "Berrendo Well",
            "gps": {"latitude": 33.4, "longitude": -104.5, "elevation": 3600.0},
        },
    ]


@pytest.fixture
def sample_data_page() -> dict[str, Any]:
    """One raw HydroVu readings page for a single location."""
    return {
        "locationId": 123,
        "parameters": [
            {
                "parameterId": "4",
                "unitId": "35",
                "readings": [
                    {"timestamp": 1748736000, "value": 45.3},
                    {"timestamp": 1748739600, "value": 45.1},
                ],
            }
        ],
    }


@pytest.fixture
def sample_friendly_names() -> dict[str, Any]:
    """The sispec friendly-names payload mapping parameter/unit ids."""
    return {
        "parameters": {"4": "Depth to Water"},
        "units": {"35": "ft"},
    }
