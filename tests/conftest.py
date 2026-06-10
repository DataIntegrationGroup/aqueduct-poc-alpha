"""Shared fixtures for the aqueduct-poc-alpha test suite."""

from __future__ import annotations

from typing import Any

import pytest


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
