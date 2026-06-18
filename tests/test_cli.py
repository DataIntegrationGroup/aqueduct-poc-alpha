"""Unit tests for the pvacd-ingest CLI (fake clients, no network/GCS)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from conftest import FakeGcsClient, FakeHydroVuClient

from aqueduct_cloud_functions import cli
from aqueduct_cloud_functions.settings import PvacdIngestSettings


@pytest.fixture
def patch_build(
    monkeypatch: pytest.MonkeyPatch,
    ingest_env: None,
    fake_gcs: FakeGcsClient,
    make_fake_hydrovu: Callable[..., FakeHydroVuClient],
) -> dict[str, Any]:
    """Patch cli.build_clients to return fakes; expose them for assertions."""
    holder: dict[str, Any] = {"hydrovu": make_fake_hydrovu(), "gcs": fake_gcs}

    def _fake_build(
        settings: PvacdIngestSettings,
    ) -> tuple[FakeHydroVuClient, FakeGcsClient]:
        """Return the pre-built fakes regardless of settings."""
        return holder["hydrovu"], holder["gcs"]

    monkeypatch.setattr(cli, "build_clients", _fake_build)
    return holder


def _result(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """Parse the JSON the CLI printed to stdout."""
    return json.loads(capsys.readouterr().out)


def test_daily_uses_default_lookback(
    patch_build: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """`daily` defaults to a 1-day window and stages every location."""
    code = cli.main(["daily"])

    assert code == 0
    body = _result(capsys)
    assert body["status"] == "ok"
    start = datetime.fromisoformat(body["start_time"])
    end = datetime.fromisoformat(body["end_time"])
    assert (end - start).days == 1

    gcs: FakeGcsClient = patch_build["gcs"]
    dt = body["dt"]
    assert f"raw/pvacd/dt={dt}/locations.json" in gcs.objects
    assert f"raw/pvacd/dt={dt}/readings/location_123.json" in gcs.objects


def test_daily_days_override(
    patch_build: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """`daily --days` widens the window."""
    code = cli.main(["daily", "--days", "3"])

    assert code == 0
    body = _result(capsys)
    start = datetime.fromisoformat(body["start_time"])
    end = datetime.fromisoformat(body["end_time"])
    assert (end - start).days == 3


def test_backfill_window(
    patch_build: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """`backfill --days 31` produces a 31-day window in one dt=<today> partition."""
    code = cli.main(["backfill", "--days", "31"])

    assert code == 0
    body = _result(capsys)
    start = datetime.fromisoformat(body["start_time"])
    end = datetime.fromisoformat(body["end_time"])
    assert (end - start).days == 31
    assert body["dt"] == end.strftime("%Y-%m-%d")


def test_range_pins_window_and_partition(
    patch_build: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """`range` pins explicit start/end and the dt partition."""
    code = cli.main(["range", "--start", "2026-05-01", "--end", "2026-06-01"])

    assert code == 0
    body = _result(capsys)
    assert body["dt"] == "2026-06-01"
    assert body["start_time"] == "2026-05-01T00:00:00+00:00"
    assert body["end_time"] == "2026-06-01T00:00:00+00:00"


def test_bad_date_returns_config_error(
    patch_build: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed date exits 2 with an error payload."""
    code = cli.main(["range", "--start", "not-a-date", "--end", "2026-06-01"])

    assert code == 2
    assert _result(capsys)["status"] == "error"


def test_inverted_window_returns_config_error(
    patch_build: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """start after end exits 2."""
    code = cli.main(["range", "--start", "2026-06-01", "--end", "2026-05-01"])

    assert code == 2


def test_all_locations_failing_returns_ingest_failure(
    patch_build: dict[str, Any],
    make_fake_hydrovu: Callable[..., FakeHydroVuClient],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If every location fails with a real error, the CLI exits 1 (status error)."""
    patch_build["hydrovu"] = make_fake_hydrovu(failing_location_ids={123, 456})
    code = cli.main(["daily"])

    assert code == 1
    assert _result(capsys)["status"] == "error"


def test_all_locations_no_data_returns_ok(
    patch_build: dict[str, Any],
    make_fake_hydrovu: Callable[..., FakeHydroVuClient],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If every location merely has no data, the CLI exits 0 (status ok)."""
    patch_build["hydrovu"] = make_fake_hydrovu(no_data_location_ids={123, 456})
    code = cli.main(["daily"])

    assert code == 0
    body = _result(capsys)
    assert body["status"] == "ok"
    assert body["locations_no_data"] == 2
    assert body["errors"] == []


def test_missing_configuration_returns_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without env vars (and no .env file) the CLI exits 2."""
    for var in ("HYDROVU_CLIENT_ID", "HYDROVU_CLIENT_SECRET", "GCS_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["daily"])

    assert code == 2
    assert _result(capsys)["status"] == "error"


def test_no_subcommand_exits_nonzero() -> None:
    """Invoking with no subcommand is an argparse usage error (SystemExit 2)."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])

    assert excinfo.value.code == 2
