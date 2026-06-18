"""
loaders/frost_loader.py

Writes CanonicalBundles to a FROST SensorThings API server over its v1.1 REST
API using ``httpx``.

Two responsibilities:
  1. ensure_datastream() - idempotent upsert of the full metadata graph:
       Location -> Thing (linked to Location) -> Sensor -> ObservedProperty
       -> Datastream (linked to all four).
     Each entity is looked up by ``properties/externalId`` before creation, and
     linked by id-only references (``{"@iot.id": id}``) so re-runs never create
     duplicates.

  2. load_observations() - posts observations in chunked Data Array batches via
     ``POST /CreateObservations``. FROST is the source of truth for de-duping:
     only readings newer than the datastream's current max ``phenomenonTime`` are
     posted, so re-running the transform is safe.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from aqueduct_cloud_functions.canonical.canonical_model import (
    CanonicalDatastream,
    CanonicalLocation,
    CanonicalObservation,
    CanonicalObservedProperty,
    CanonicalSensor,
    CanonicalThing,
)

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1000
KEY_FIELD = "externalId"

# Matches the id inside a FROST entity URL, e.g. ".../Things(5)" -> "5".
_ID_IN_URL = re.compile(r"\(([^)]+)\)/?$")


@dataclass
class LoadResult:
    """Per-datastream outcome of :meth:`FrostLoader.load_observations`."""

    datastream_key: str
    considered: int = 0
    posted: int = 0
    skipped: int = 0
    new_watermark: datetime | None = None


def _chunked[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    """Split ``items`` into consecutive chunks of at most ``size``."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _with_retry[T](
    fn: Callable[..., T], *args: object, attempts: int = 5, base_delay: float = 0.5
) -> T:
    """Call ``fn(*args)``, retrying with exponential backoff on any exception."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 - retry every transport/HTTP error
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "FROST call failed (%d/%d): %s - retry in %.1fs",
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _as_id(value: str) -> int | str:
    """Coerce a numeric FROST id to int (its default id type), else pass through."""
    return int(value) if value.isdigit() else value


def _iso_z(moment: datetime) -> str:
    """Render a UTC datetime as ISO 8601 with a trailing ``Z``."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_phenomenon_time(raw: str) -> datetime:
    """Parse a FROST ``phenomenonTime`` instant into a UTC-aware datetime."""
    # A datastream's phenomenonTime can be an interval "start/end"; take the end.
    instant = raw.split("/")[-1]
    return datetime.fromisoformat(instant.replace("Z", "+00:00"))


class FrostLoader:
    """Loads canonical datastreams and observations into FROST over httpx."""

    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        """Bind to a FROST service root; an injected client keeps tests offline."""
        self._base = self._normalize_base(base_url)
        self._client = client or httpx.Client(timeout=30.0)
        self._chunk_size = chunk_size

    @staticmethod
    def _normalize_base(base_url: str) -> str:
        """Ensure the base URL ends with the ``/v1.1`` SensorThings version path."""
        trimmed = base_url.rstrip("/")
        if not trimmed.endswith("/v1.1"):
            trimmed = f"{trimmed}/v1.1"
        return trimmed

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    # ── metadata upsert ───────────────────────────────────────────────────────

    def ensure_datastream(self, spec: CanonicalDatastream) -> str:
        """Idempotently upsert the metadata graph; return the FROST Datastream id."""
        location_id = self._upsert(
            "Locations",
            spec.thing.location.external_key,
            lambda: self._location_body(spec.thing.location),
        )
        thing_id = self._upsert(
            "Things",
            spec.thing.external_key,
            lambda: self._thing_body(spec.thing, location_id),
        )
        sensor_id = self._upsert(
            "Sensors",
            spec.sensor.external_key,
            lambda: self._sensor_body(spec.sensor),
        )
        obsprop_id = self._upsert(
            "ObservedProperties",
            spec.observed_property.external_key,
            lambda: self._observed_property_body(spec.observed_property),
        )
        return self._upsert(
            "Datastreams",
            spec.external_key,
            lambda: self._datastream_body(spec, thing_id, sensor_id, obsprop_id),
        )

    def _upsert(
        self, entity_set: str, external_key: str, build_body: Callable[[], dict]
    ) -> str:
        """Return an existing entity id by external key, or create it."""
        existing = self._find_id(entity_set, external_key)
        if existing is not None:
            return existing
        return self._create(entity_set, build_body(), external_key)

    def _find_id(self, entity_set: str, external_key: str) -> str | None:
        """Look up an entity id by its ``properties/externalId``."""
        resp = self._client.get(
            f"{self._base}/{entity_set}",
            params={
                "$filter": f"properties/{KEY_FIELD} eq '{external_key}'",
                "$select": "@iot.id",
                "$top": 1,
            },
        )
        resp.raise_for_status()
        values = resp.json().get("value", [])
        if not values:
            return None
        return str(values[0]["@iot.id"])

    def _create(self, entity_set: str, body: dict, external_key: str) -> str:
        """POST a new entity and return its id (from the Location header)."""
        resp = self._client.post(f"{self._base}/{entity_set}", json=body)
        resp.raise_for_status()
        new_id = self._id_from_response(resp)
        if new_id is None:
            # Fall back to a lookup if the server omitted a Location header.
            new_id = self._find_id(entity_set, external_key)
        if new_id is None:
            raise RuntimeError(
                f"Could not resolve id for created {entity_set} entity "
                f"(externalId={external_key})"
            )
        logger.info("Created %s id=%s key=%s", entity_set, new_id, external_key)
        return new_id

    @staticmethod
    def _id_from_response(resp: httpx.Response) -> str | None:
        """Extract the new entity id from the FROST ``Location`` header."""
        location = resp.headers.get("Location") or resp.headers.get("location")
        if not location:
            return None
        match = _ID_IN_URL.search(location)
        if not match:
            return None
        return match.group(1).strip("'\"")

    # ── entity body builders ──────────────────────────────────────────────────

    def _location_body(self, spec: CanonicalLocation) -> dict:
        """Build the SensorThings Location request body."""
        return {
            "name": spec.name,
            "description": spec.description,
            "encodingType": spec.encoding_type,
            "location": spec.geometry,
            "properties": {**spec.properties, KEY_FIELD: spec.external_key},
        }

    def _thing_body(self, spec: CanonicalThing, location_id: str) -> dict:
        """Build the Thing body, linking the Location by id only."""
        return {
            "name": spec.name,
            "description": spec.description,
            "properties": {**spec.properties, KEY_FIELD: spec.external_key},
            "Locations": [{"@iot.id": _as_id(location_id)}],
        }

    def _sensor_body(self, spec: CanonicalSensor) -> dict:
        """Build the SensorThings Sensor request body."""
        return {
            "name": spec.name,
            "description": spec.description,
            "encodingType": spec.encoding_type,
            "metadata": spec.metadata,
            "properties": {**spec.properties, KEY_FIELD: spec.external_key},
        }

    def _observed_property_body(self, spec: CanonicalObservedProperty) -> dict:
        """Build the SensorThings ObservedProperty request body."""
        return {
            "name": spec.name,
            "definition": spec.definition,
            "description": spec.description,
            "properties": {**spec.properties, KEY_FIELD: spec.external_key},
        }

    def _datastream_body(
        self,
        spec: CanonicalDatastream,
        thing_id: str,
        sensor_id: str,
        observed_property_id: str,
    ) -> dict:
        """Build the Datastream body, linking Thing/Sensor/ObservedProperty by id."""
        return {
            "name": spec.name,
            "description": spec.description,
            "observationType": spec.observation_type,
            "unitOfMeasurement": spec.unit_of_measurement,
            "properties": {**spec.properties, KEY_FIELD: spec.external_key},
            "Thing": {"@iot.id": _as_id(thing_id)},
            "Sensor": {"@iot.id": _as_id(sensor_id)},
            "ObservedProperty": {"@iot.id": _as_id(observed_property_id)},
        }

    # ── observation loading ───────────────────────────────────────────────────

    def load_observations(
        self,
        datastream_key: str,
        datastream_id: str,
        observations: Sequence[CanonicalObservation],
    ) -> LoadResult:
        """Post observations newer than FROST's current max, in chunked batches."""
        ordered = sorted(observations, key=lambda o: o.phenomenon_time)
        result = LoadResult(datastream_key=datastream_key, considered=len(ordered))

        watermark = self._max_phenomenon_time(datastream_id)
        if watermark is not None:
            kept = [o for o in ordered if o.phenomenon_time > watermark]
            result.skipped = len(ordered) - len(kept)
            ordered = kept

        result.new_watermark = watermark
        if not ordered:
            return result

        for chunk in _chunked(ordered, self._chunk_size):
            _with_retry(self._post_data_array, datastream_id, chunk)
            result.posted += len(chunk)
            result.new_watermark = chunk[-1].phenomenon_time

        logger.info(
            "datastream %s: posted %d, skipped %d, watermark->%s",
            datastream_key,
            result.posted,
            result.skipped,
            result.new_watermark,
        )
        return result

    def _post_data_array(
        self, datastream_id: str, chunk: Sequence[CanonicalObservation]
    ) -> None:
        """POST one Data Array batch of observations to ``/CreateObservations``."""
        body: list[dict[str, Any]] = [
            {
                "Datastream": {"@iot.id": _as_id(datastream_id)},
                "components": ["phenomenonTime", "result"],
                "dataArray": [[_iso_z(o.phenomenon_time), o.result] for o in chunk],
            }
        ]
        resp = self._client.post(f"{self._base}/CreateObservations", json=body)
        resp.raise_for_status()

    def _max_phenomenon_time(self, datastream_id: str) -> datetime | None:
        """Return the newest observation time on a datastream, or None if empty."""
        try:
            resp = self._client.get(
                f"{self._base}/Datastreams({datastream_id})/Observations",
                params={
                    "$orderby": "phenomenonTime desc",
                    "$top": 1,
                    "$select": "phenomenonTime",
                },
            )
            resp.raise_for_status()
            values = resp.json().get("value", [])
            if values and values[0].get("phenomenonTime"):
                return _parse_phenomenon_time(values[0]["phenomenonTime"])
        except Exception as exc:  # noqa: BLE001 - a missing watermark is non-fatal
            logger.warning(
                "Could not read watermark for datastream %s: %s", datastream_id, exc
            )
        return None
