"""Unit tests for HydroVuAdapter - staged record -> canonical model mapping.

No network or GCS: the adapter receives pre-grouped records (the shape
``pvacd_transform.read_staged_records`` produces) and maps them to the canonical
model. Confirms the data mapping required by the acceptance criteria.

Parameter ids:
  "4"  = Depth to Water (metres -> feet)
  "1"  = Temperature (skipped)
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from aqueduct_cloud_functions.adapters.hydrovu_adapter import (
    METRES_TO_FEET,
    HydroVuAdapter,
)

DTW_READING = {
    "parameter_id": "4",
    "unit_id": "35",
    "timestamp": 1748736000,
    "value": 10.0,
}
TEMP_READING = {
    "parameter_id": "1",
    "unit_id": "1",
    "timestamp": 1748736000,
    "value": 22.5,
}


def _record(
    location_id: int = 123,
    location_name: str = "Zumwalt Well",
    latitude: float = 36.1,
    longitude: float = -106.2,
    readings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a grouped location record, defaulting to a single DTW reading."""
    return {
        "location_id": location_id,
        "location_name": location_name,
        "latitude": latitude,
        "longitude": longitude,
        "readings": [DTW_READING] if readings is None else readings,
    }


class TestToThing:
    """Mapping a record to a CanonicalThing + CanonicalLocation."""

    def test_external_key_is_pvacd_location_id(self) -> None:
        """The Thing key joins the lowercased agency with the location id."""
        thing = HydroVuAdapter([]).to_thing(_record(location_id=123))
        assert thing.external_key == "pvacd-123"

    def test_location_key_matches_thing(self) -> None:
        """The nested Location shares the Thing's external key."""
        thing = HydroVuAdapter([]).to_thing(_record())
        assert thing.location.external_key == thing.external_key

    def test_agency_and_source_id_in_properties(self) -> None:
        """Thing properties carry the agency code and the source id."""
        thing = HydroVuAdapter([]).to_thing(_record(location_id=123))
        assert thing.properties["agency"] == "PVACD"
        assert thing.properties["source_id"] == 123

    def test_geometry_is_lon_lat_point(self) -> None:
        """Geometry is a GeoJSON Point in [lon, lat] order."""
        geom = (
            HydroVuAdapter([])
            .to_thing(_record(latitude=36.1, longitude=-106.2))
            .location.geometry
        )
        assert geom["type"] == "Point"
        assert geom["coordinates"] == [-106.2, 36.1]

    def test_location_name_preserved(self) -> None:
        """The Location keeps the HydroVu location name."""
        thing = HydroVuAdapter([]).to_thing(_record(location_name="Zumwalt Well"))
        assert thing.location.name == "Zumwalt Well"


class TestToObservations:
    """Mapping readings to CanonicalObservations."""

    def test_returns_only_dtw_readings(self) -> None:
        """Non-DTW parameters are filtered out."""
        obs = HydroVuAdapter([]).to_observations(
            _record(readings=[DTW_READING, TEMP_READING])
        )
        assert len(obs) == 1

    def test_skips_non_dtw(self) -> None:
        """A record with only temperature yields no observations."""
        assert (
            HydroVuAdapter([]).to_observations(_record(readings=[TEMP_READING])) == []
        )

    def test_converts_metres_to_feet(self) -> None:
        """DTW values are converted from metres to feet."""
        obs = HydroVuAdapter([]).to_observations(
            _record(readings=[{**DTW_READING, "value": 10.0}])
        )
        assert abs(obs[0].result - 10.0 * METRES_TO_FEET) < 1e-6

    def test_phenomenon_time_is_utc(self) -> None:
        """Observation timestamps are UTC-aware."""
        obs = HydroVuAdapter([]).to_observations(_record(readings=[DTW_READING]))
        assert obs[0].phenomenon_time.tzinfo == UTC
        assert obs[0].phenomenon_time.timestamp() == 1748736000

    def test_datastream_key_format(self) -> None:
        """Observations point at the well's depth-to-water datastream."""
        obs = HydroVuAdapter([]).to_observations(_record(location_id=123))
        assert obs[0].datastream_external_key == "pvacd-123-dtw"


class TestBuildDatastreams:
    """Building the depth-to-water datastream for a Thing."""

    def _datastream(self) -> Any:
        adapter = HydroVuAdapter([])
        thing = adapter.to_thing(_record(location_id=123))
        datastreams = adapter._build_datastreams(thing)
        assert len(datastreams) == 1
        return datastreams[0]

    def test_external_key_format(self) -> None:
        """The datastream key is pvacd-{id}-dtw."""
        assert self._datastream().external_key == "pvacd-123-dtw"

    def test_unit_is_feet(self) -> None:
        """The datastream stores observations in feet."""
        uom = self._datastream().unit_of_measurement
        assert uom["symbol"] == "ft"
        assert uom["name"] == "foot"


class TestRun:
    """End-to-end record -> CanonicalBundle behavior."""

    def test_one_bundle_per_location(self) -> None:
        """Each input record produces one bundle."""
        records = [_record(location_id=1), _record(location_id=2)]
        assert len(list(HydroVuAdapter(records).run())) == 2

    def test_bundle_observations_keyed_by_datastream(self) -> None:
        """Bundle observations are keyed by the datastream external key."""
        bundles = list(HydroVuAdapter([_record(location_id=123)]).run())
        assert "pvacd-123-dtw" in bundles[0].observations
        assert len(bundles[0].observations["pvacd-123-dtw"]) == 1

    def test_empty_records_yields_no_bundles(self) -> None:
        """No records means no bundles."""
        assert list(HydroVuAdapter([]).run()) == []
