"""Tests for the staging -> canonical -> FROST transform orchestration.

Uses the seeded ``staged_gcs`` fixture (no network) and a fake loader to verify
that staged JSON is reshaped and grouped correctly (data mapping) and that the
adapter/loader counts add up (data integrity).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aqueduct_cloud_functions.canonical.canonical_model import (
    CanonicalDatastream,
    CanonicalObservation,
)
from aqueduct_cloud_functions.loaders import LoadResult
from aqueduct_cloud_functions.pvacd_transform import (
    read_staged_records,
    run_pvacd_to_frost,
    transform_failed,
)


class FakeFrostLoader:
    """Records ensure/load calls and reports every observation as posted."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.ensured: list[str] = []
        self.loaded: list[tuple[str, list[CanonicalObservation]]] = []

    def ensure_datastream(self, spec: CanonicalDatastream) -> str:
        """Record the datastream and hand back a synthetic id."""
        self.ensured.append(spec.external_key)
        return str(len(self.ensured))

    def load_observations(
        self,
        datastream_key: str,
        datastream_id: str,
        observations: Sequence[CanonicalObservation],
    ) -> LoadResult:
        """Record the observations and report them all as posted."""
        obs = list(observations)
        self.loaded.append((datastream_key, obs))
        return LoadResult(
            datastream_key=datastream_key,
            considered=len(obs),
            posted=len(obs),
        )


class FailingFrostLoader:
    """Fails every metadata upsert - stands in for an unreachable FROST."""

    def ensure_datastream(self, spec: CanonicalDatastream) -> str:
        """Always fail."""
        raise RuntimeError("frost down")


class TestReadStagedRecords:
    """Reshaping staged GCS JSON into grouped per-location records."""

    def test_skips_locations_without_dtw(self, staged_gcs: Any, staged_dt: str) -> None:
        """Only the well with depth-to-water readings becomes a record."""
        records = read_staged_records(staged_gcs, staged_dt)
        assert len(records) == 1
        assert records[0]["location_id"] == 123

    def test_joins_location_metadata(self, staged_gcs: Any, staged_dt: str) -> None:
        """The record carries the joined name and coordinates."""
        record = read_staged_records(staged_gcs, staged_dt)[0]
        assert record["location_name"] == "Zumwalt Well"
        assert record["latitude"] == 36.1
        assert record["longitude"] == -106.2

    def test_flattens_pages_into_readings(
        self, staged_gcs: Any, staged_dt: str
    ) -> None:
        """Nested HydroVu pages are flattened into flat reading rows."""
        readings = read_staged_records(staged_gcs, staged_dt)[0]["readings"]
        dtw = [r for r in readings if r["parameter_id"] == "4"]
        assert len(dtw) == 2
        assert {r["parameter_id"] for r in readings} == {"4", "1"}


class TestRunPvacdToFrost:
    """Driving the adapter and loader end to end over staged data."""

    def test_counts_reflect_loaded_data(self, staged_gcs: Any, staged_dt: str) -> None:
        """One well, one datastream, two DTW observations are reported."""
        frost = FakeFrostLoader()
        result = run_pvacd_to_frost(staged_gcs, frost, staged_dt)  # type: ignore[arg-type]

        assert result["status"] == "ok"
        assert result["locations_count"] == 1
        assert result["things_loaded"] == 1
        assert result["datastreams_loaded"] == 1
        assert result["observations_posted"] == 2
        assert result["errors"] == []
        assert transform_failed(result) is False

    def test_drives_loader_with_canonical_datastream(
        self, staged_gcs: Any, staged_dt: str
    ) -> None:
        """The loader is asked to ensure the well's DTW datastream and obs."""
        frost = FakeFrostLoader()
        run_pvacd_to_frost(staged_gcs, frost, staged_dt)  # type: ignore[arg-type]

        assert frost.ensured == ["pvacd-123-dtw"]
        key, observations = frost.loaded[0]
        assert key == "pvacd-123-dtw"
        assert len(observations) == 2

    def test_collects_errors_and_reports_failure(
        self, staged_gcs: Any, staged_dt: str
    ) -> None:
        """A failing loader leaves nothing loaded and marks the run failed."""
        result = run_pvacd_to_frost(staged_gcs, FailingFrostLoader(), staged_dt)  # type: ignore[arg-type]

        assert result["datastreams_loaded"] == 0
        assert result["errors"][0]["datastream"] == "pvacd-123-dtw"
        assert transform_failed(result) is True
