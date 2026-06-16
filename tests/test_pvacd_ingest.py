"""Unit tests for the pvacd_ingest handler (fake clients, no network/GCS)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

import pytest
from conftest import FakeGcsClient, FakeHydroVuClient
from flask import Request
from werkzeug.test import EnvironBuilder

import main
from aqueduct_cloud_functions.settings import PvacdIngestSettings


def make_request(
    json_body: dict[str, Any] | None = None,
    query_string: dict[str, str] | None = None,
) -> Request:
    """Build a flask Request without running a server."""
    builder = EnvironBuilder(
        method="POST", json=json_body or {}, query_string=query_string
    )
    return Request(builder.get_environ())


def invoke(request: Request) -> tuple[dict[str, Any], int]:
    """Call the handler and narrow its (body, status) return for assertions."""
    return cast(tuple[dict[str, Any], int], main.pvacd_ingest(request))


@pytest.fixture
def fake_clients(
    monkeypatch: pytest.MonkeyPatch,
    ingest_env: None,
    fake_gcs: FakeGcsClient,
    make_fake_hydrovu: Callable[..., FakeHydroVuClient],
) -> dict[str, Any]:
    """Patch main.build_clients to return fakes; expose them for assertions."""
    holder: dict[str, Any] = {"hydrovu": make_fake_hydrovu(), "gcs": fake_gcs}

    def _fake_build(
        settings: PvacdIngestSettings,
    ) -> tuple[FakeHydroVuClient, FakeGcsClient]:
        """Return the pre-built fakes regardless of settings."""
        return holder["hydrovu"], holder["gcs"]

    monkeypatch.setattr(main, "build_clients", _fake_build)
    return holder


def test_default_window_ingests_all_locations(fake_clients: dict[str, Any]) -> None:
    """A bare POST stages locations, friendly names, and per-location readings."""
    body, status = invoke(make_request())

    assert status == 200
    assert body["status"] == "ok"
    assert body["locations_count"] == 2
    assert body["readings_objects_written"] == 2
    assert body["errors"] == []
    start = datetime.fromisoformat(body["start_time"])
    end = datetime.fromisoformat(body["end_time"])
    assert (end - start).days == 1

    gcs: FakeGcsClient = fake_clients["gcs"]
    dt = body["dt"]
    assert f"raw/pvacd/dt={dt}/locations.json" in gcs.objects
    assert f"raw/pvacd/dt={dt}/friendly_names.json" in gcs.objects
    assert f"raw/pvacd/dt={dt}/readings/location_123.json" in gcs.objects
    assert f"raw/pvacd/dt={dt}/readings/location_456.json" in gcs.objects


def test_lookback_days_override(fake_clients: dict[str, Any]) -> None:
    """lookback_days in the JSON body widens the window."""
    body, status = invoke(make_request(json_body={"lookback_days": 31}))

    assert status == 200
    start = datetime.fromisoformat(body["start_time"])
    end = datetime.fromisoformat(body["end_time"])
    assert (end - start).days == 31


def test_explicit_dates(fake_clients: dict[str, Any]) -> None:
    """Explicit start_date/end_date pin the window and the dt partition."""
    body, status = invoke(
        make_request(json_body={"start_date": "2026-05-01", "end_date": "2026-06-01"})
    )

    assert status == 200
    assert body["dt"] == "2026-06-01"
    assert body["start_time"] == "2026-05-01T00:00:00+00:00"
    assert body["end_time"] == "2026-06-01T00:00:00+00:00"


def test_query_string_params(fake_clients: dict[str, Any]) -> None:
    """Window params are also accepted from the query string."""
    body, status = invoke(make_request(query_string={"lookback_days": "2"}))

    assert status == 200
    start = datetime.fromisoformat(body["start_time"])
    end = datetime.fromisoformat(body["end_time"])
    assert (end - start).days == 2


def test_bad_date_returns_400(fake_clients: dict[str, Any]) -> None:
    """A malformed date yields a 400 with an error message."""
    body, status = invoke(make_request(json_body={"start_date": "not-a-date"}))

    assert status == 400
    assert body["status"] == "error"


def test_inverted_window_returns_400(fake_clients: dict[str, Any]) -> None:
    """start_date after end_date is rejected."""
    body, status = invoke(
        make_request(json_body={"start_date": "2026-06-01", "end_date": "2026-05-01"})
    )

    assert status == 400


def test_partial_failure_returns_200_with_errors(
    fake_clients: dict[str, Any],
    make_fake_hydrovu: Callable[..., FakeHydroVuClient],
) -> None:
    """One failing location is reported in errors; the rest still stage."""
    fake_clients["hydrovu"] = make_fake_hydrovu(failing_location_ids={456})
    body, status = invoke(make_request())

    assert status == 200
    assert body["readings_objects_written"] == 1
    assert len(body["errors"]) == 1
    assert body["errors"][0]["location_id"] == 456


def test_all_locations_failing_returns_502(
    fake_clients: dict[str, Any],
    make_fake_hydrovu: Callable[..., FakeHydroVuClient],
) -> None:
    """If every location fails, the run is reported as a 502."""
    fake_clients["hydrovu"] = make_fake_hydrovu(failing_location_ids={123, 456})
    body, status = invoke(make_request())

    assert status == 502
    assert body["status"] == "error"
    assert len(body["errors"]) == 2


def test_missing_configuration_returns_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Without env vars (and no .env file) the handler reports a 500."""
    for var in ("HYDROVU_CLIENT_ID", "HYDROVU_CLIENT_SECRET", "GCS_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    body, status = invoke(make_request())

    assert status == 500
    assert body["status"] == "error"
