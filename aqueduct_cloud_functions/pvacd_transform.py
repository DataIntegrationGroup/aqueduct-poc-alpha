"""Core PVACD staging -> canonical -> FROST transform logic.

Flask-independent so it can be reused by both entry points in this POC: the
Cloud Function HTTP handler (``main.py::pvacd_to_frost``) and the CLI
(``aqueduct_cloud_functions.transform_cli``). Each is a thin wrapper that
resolves a ``dt`` partition and calls :func:`run_pvacd_to_frost`.

Pipeline:
  1. read_staged_records() - read the staged GCS JSON for a ``dt`` partition and
     reshape it into one grouped record per location (the shape HydroVuAdapter
     consumes). This is alpha's analogue of bravo's
     ``transform_hydrovu._group_by_location``.
  2. HydroVuAdapter(records).run() - map records to CanonicalBundles.
  3. FrostLoader - upsert metadata and load observations per datastream.
"""

from __future__ import annotations

import logging
from typing import Any

from aqueduct_cloud_functions.adapters.hydrovu_adapter import (
    DTW_PARAMETER_ID,
    HydroVuAdapter,
)
from aqueduct_cloud_functions.clients import GcsStagingClient
from aqueduct_cloud_functions.loaders import FrostLoader
from aqueduct_cloud_functions.settings import FrostLoadSettings

logger = logging.getLogger(__name__)


def build_clients(settings: FrostLoadSettings) -> tuple[GcsStagingClient, FrostLoader]:
    """Build the GCS reader and FROST loader; single seam for test injection."""
    gcs = GcsStagingClient(bucket_name=settings.gcs_bucket_name)
    frost = FrostLoader(base_url=settings.frost_service_root_url)
    return gcs, frost


def read_staged_records(gcs: GcsStagingClient, dt: str) -> list[dict[str, Any]]:
    """Read staged PVACD JSON for ``dt`` and group it into one record per location.

    Joins each ``readings/location_{id}.json`` file to its ``locations.json``
    metadata and flattens the raw HydroVu ``pages`` into flat reading rows.
    Locations with no depth-to-water readings in the window are skipped.
    """
    prefix = f"raw/pvacd/dt={dt}"
    locations = gcs.read_json(f"{prefix}/locations.json")
    location_by_id = {loc["id"]: loc for loc in locations}

    records: list[dict[str, Any]] = []
    for object_path in gcs.list_objects(f"{prefix}/readings/"):
        if not object_path.endswith(".json"):
            continue
        payload = gcs.read_json(object_path)
        location_id = payload["location_id"]

        readings: list[dict[str, Any]] = []
        for page in payload.get("pages", []):
            for parameter in page.get("parameters", []):
                for reading in parameter.get("readings", []):
                    readings.append(
                        {
                            "parameter_id": parameter["parameterId"],
                            "unit_id": parameter["unitId"],
                            "timestamp": reading["timestamp"],
                            "value": reading["value"],
                        }
                    )

        if not any(r["parameter_id"] == DTW_PARAMETER_ID for r in readings):
            logger.info("transform location=%s no DTW readings - skipping", location_id)
            continue

        location = location_by_id.get(location_id, {})
        gps = location.get("gps", {})
        records.append(
            {
                "location_id": location_id,
                "location_name": location.get("name", ""),
                "latitude": gps.get("latitude"),
                "longitude": gps.get("longitude"),
                "readings": readings,
            }
        )

    logger.info("transform dt=%s grouped %d location record(s)", dt, len(records))
    return records


def run_pvacd_to_frost(
    gcs: GcsStagingClient, frost: FrostLoader, dt: str
) -> dict[str, Any]:
    """Transform a staged ``dt`` partition into FROST and report counts.

    A per-datastream failure is collected and the rest still load; the run only
    reports overall failure (via :func:`transform_failed`) when errors prevented
    every datastream from loading.
    """
    records = read_staged_records(gcs, dt)
    bundles = HydroVuAdapter(records).run()

    errors: list[dict[str, Any]] = []
    things_loaded = 0
    datastreams_loaded = 0
    observations_posted = 0
    observations_skipped = 0

    for bundle in bundles:
        things_loaded += 1
        for datastream in bundle.datastreams:
            try:
                datastream_id = frost.ensure_datastream(datastream)
            except Exception as exc:  # noqa: BLE001 - collect, keep loading others
                logger.error(
                    "transform datastream=%s ensure failed: %s",
                    datastream.external_key,
                    exc,
                )
                errors.append(
                    {"datastream": datastream.external_key, "error": str(exc)}
                )
                continue

            datastreams_loaded += 1
            observations = bundle.observations.get(datastream.external_key, [])
            try:
                result = frost.load_observations(
                    datastream.external_key, datastream_id, observations
                )
            except Exception as exc:  # noqa: BLE001 - collect, keep loading others
                logger.error(
                    "transform datastream=%s load failed: %s",
                    datastream.external_key,
                    exc,
                )
                errors.append(
                    {"datastream": datastream.external_key, "error": str(exc)}
                )
                continue

            observations_posted += result.posted
            observations_skipped += result.skipped

    return {
        "status": "ok",
        "dt": dt,
        "locations_count": len(records),
        "things_loaded": things_loaded,
        "datastreams_loaded": datastreams_loaded,
        "observations_posted": observations_posted,
        "observations_skipped": observations_skipped,
        "errors": errors,
    }


def transform_failed(result: dict[str, Any]) -> bool:
    """True when errors prevented every datastream from loading.

    A run where some datastreams loaded (or where there were simply no records)
    is not a failure.
    """
    return bool(result["errors"]) and result["datastreams_loaded"] == 0
