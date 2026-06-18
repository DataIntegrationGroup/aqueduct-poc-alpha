"""
adapters/hydrovu_adapter.py

Mapping-only adapter for HydroVu data.
Raw records come from GCS (written by the PVACD ingest Cloud Function) and are
reshaped into one grouped record per location by
``aqueduct_cloud_functions.pvacd_transform.read_staged_records`` before reaching
this adapter - fetching and auth live in the ingest function, not here.

Responsibilities:
  - extract()            yield the pre-grouped records - called by run()
  - to_thing()           map a raw location record → CanonicalThing + CanonicalLocation
  - to_observations()    map raw readings → list[CanonicalObservation]
  - _build_datastreams() build the CanonicalDatastream for this Thing

Record shape expected by this adapter (one per location):
  {
    "location_id":   int,
    "location_name": str,
    "latitude":      float,
    "longitude":     float,
    "readings": [
      {"parameter_id": "4", "unit_id": "35", "timestamp": int, "value": float},
      ...
    ],
  }

Mapping confirmed against the HydroVu /sispec/friendlynames endpoint and the
prior STAO implementation:
  parameterId "4"  = Level: Depth to Water
  unitId      "35" = metres -> converted to feet (* 3.28084)
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from aqueduct_cloud_functions.canonical.base_adapter import BaseAdapter
from aqueduct_cloud_functions.canonical.canonical_constants import (
    DTW_OBS_PROP,
    HYDROVU_SENSOR,
    UNIT_FOOT,
    OM_Measurement,
    gwl_datastream_meta,
)
from aqueduct_cloud_functions.canonical.canonical_model import (
    CanonicalDatastream,
    CanonicalLocation,
    CanonicalObservation,
    CanonicalThing,
)

logger = logging.getLogger(__name__)

AGENCY = "PVACD"

# Confirmed via HydroVu /sispec/friendlynames and the prior STAO implementation.
DTW_PARAMETER_ID = "4"
METRES_TO_FEET = 3.28084


class HydroVuAdapter(BaseAdapter):
    """Maps grouped HydroVu records (DTW) staged in GCS to the canonical model.

    external_key convention: ``pvacd-{location_id}`` (stable HydroVu integer id).
    """

    def __init__(self, records: list[dict]) -> None:
        """Initialize with pre-grouped records (one per location)."""
        super().__init__(agency=AGENCY)
        self._records = records

    def extract(self) -> Iterator[dict]:
        """Yield one pre-grouped record per location."""
        yield from self._records

    def to_thing(self, record: dict) -> CanonicalThing:
        """Map a raw location record to a CanonicalThing with its CanonicalLocation."""
        source_id = str(record["location_id"])
        external_key = self.make_location_key(source_id)
        properties = {"agency": self.agency, "source_id": record["location_id"]}

        location = CanonicalLocation(
            external_key=external_key,
            name=record["location_name"],
            description="Location of well where measurements are made",
            geometry={
                "type": "Point",
                "coordinates": [record["longitude"], record["latitude"]],
            },
            properties=properties,
        )

        return CanonicalThing(
            external_key=external_key,
            name="Water Well",
            description=(
                "Well drilled or set into the subsurface for pumping water or "
                "monitoring groundwater"
            ),
            location=location,
            properties=properties,
        )

    def to_observations(self, record: dict) -> list[CanonicalObservation]:
        """Map raw DTW readings to CanonicalObservations (metres -> feet, UTC)."""
        source_id = str(record["location_id"])
        ds_key = self.make_datastream_key(source_id, "dtw")

        observations: list[CanonicalObservation] = []
        for reading in record.get("readings", []):
            if reading["parameter_id"] != DTW_PARAMETER_ID:
                continue
            observations.append(
                CanonicalObservation(
                    phenomenon_time=datetime.fromtimestamp(
                        reading["timestamp"], tz=UTC
                    ),
                    result=reading["value"] * METRES_TO_FEET,
                    datastream_external_key=ds_key,
                )
            )
        return observations

    def _build_datastreams(self, thing: CanonicalThing) -> list[CanonicalDatastream]:
        """Build the single depth-to-water CanonicalDatastream for this Thing."""
        source_id = str(thing.properties["source_id"])
        ds_key = self.make_datastream_key(source_id, "dtw")
        meta = gwl_datastream_meta(self.agency, thing.location.name)

        return [
            CanonicalDatastream(
                external_key=ds_key,
                name=meta["name"],
                description=meta["description"],
                observation_type=OM_Measurement,
                unit_of_measurement=UNIT_FOOT,
                thing=thing,
                sensor=HYDROVU_SENSOR,
                observed_property=DTW_OBS_PROP,
            )
        ]
