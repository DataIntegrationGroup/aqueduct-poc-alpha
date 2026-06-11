"""
adapters/cabq_adapter.py

Mapping-only adapter for CABQ data.
Raw records come from GCS (written by the CABQ ingest Cloud Function).

Responsibilities:
  - to_thing()           map a raw location record → CanonicalThing + CanonicalLocation
  - to_observations()    map raw readings → list[CanonicalObservation]
  - _build_datastreams() build CanonicalDatastream for this Thing
  - extract()            reads from GCS — called by run()

Fetching and auth live in the CABQ ingest function, not here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from aqueduct_cloud_functions.canonical.base_adapter import BaseAdapter
from aqueduct_cloud_functions.canonical.canonical_model import (
    CanonicalDatastream,
    CanonicalObservation,
    CanonicalThing,
)
from aqueduct_cloud_functions.canonical.canonical_constants import (
    MANUAL_SENSOR,  # noqa: F401
    DTW_OBS_PROP,  # noqa: F401
    OM_Measurement,  # noqa: F401
    UNIT_FOOT,  # noqa: F401
    gwl_datastream_meta,  # noqa: F401
)

logger = logging.getLogger(__name__)
AGENCY = "CABQ"


class CabqAdapter(BaseAdapter):
    """Maps raw CABQ records staged in GCS to the canonical model."""

    def __init__(self) -> None:
        """Initialize the adapter with the CABQ agency code."""
        super().__init__(agency=AGENCY)

    def extract(self) -> Iterator[dict]:
        """Read raw records from GCS and yield one record per location."""
        # TODO: read from GCS and yield one record per location
        raise NotImplementedError

    def to_thing(self, record: dict) -> CanonicalThing:
        """Map a raw location record to a CanonicalThing with its CanonicalLocation."""
        # TODO: map location record → CanonicalThing + CanonicalLocation
        raise NotImplementedError

    def to_observations(self, record: dict) -> list[CanonicalObservation]:
        """Map raw readings to a list of CanonicalObservations."""
        # TODO: map readings → list[CanonicalObservation]
        raise NotImplementedError

    def _build_datastreams(self, thing: CanonicalThing) -> list[CanonicalDatastream]:
        """Build the CanonicalDatastreams for this Thing from canonical constants."""
        # TODO: build CanonicalDatastream using canonical constants
        raise NotImplementedError
