"""Core PVACD HydroVu → GCS ingest logic.

Flask-independent so it can be reused by both entry points in this POC: the
Cloud Function HTTP handler (``main.py``) and the CLI
(``aqueduct_cloud_functions.cli``). The handler and CLI are thin wrappers that
resolve a time window and call :func:`run_pvacd_ingest`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from aqueduct_cloud_functions.clients import (
    GcsStagingClient,
    HydroVuApiError,
    HydroVuClient,
)
from aqueduct_cloud_functions.settings import PvacdIngestSettings

logger = logging.getLogger(__name__)


def build_clients(
    settings: PvacdIngestSettings,
) -> tuple[HydroVuClient, GcsStagingClient]:
    """Build the HydroVu and GCS clients; single seam for test injection."""
    hydrovu = HydroVuClient(
        client_id=settings.hydrovu_client_id,
        client_secret=settings.hydrovu_client_secret,
        token_url=settings.hydrovu_token_url,
        api_base_url=settings.hydrovu_api_base_url,
    )
    gcs = GcsStagingClient(bucket_name=settings.gcs_bucket_name)
    return hydrovu, gcs


def resolve_window(
    start_date: str | None,
    end_date: str | None,
    lookback_days: int | str | None,
    default_lookback_days: int,
) -> tuple[datetime, datetime]:
    """Resolve the ``[start, end)`` UTC window from explicit parameters.

    ``start_date``/``end_date`` are ``YYYY-MM-DD`` strings; ``lookback_days`` is
    an int (or numeric string). Defaults to ``end = now`` and
    ``start = end - default_lookback_days``. An explicit ``start_date`` wins over
    ``lookback_days``.

    Raises ValueError on malformed values or an empty/negative window.
    """
    end = datetime.now(tz=UTC)
    if end_date is not None:
        end = datetime.strptime(str(end_date), "%Y-%m-%d").replace(tzinfo=UTC)

    if start_date is not None:
        start = datetime.strptime(str(start_date), "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        days = default_lookback_days if lookback_days is None else int(lookback_days)
        if days <= 0:
            raise ValueError("lookback_days must be a positive integer")
        start = end - timedelta(days=days)

    if start >= end:
        raise ValueError("start_date must be before end_date")
    return start, end


def run_pvacd_ingest(
    hydrovu: HydroVuClient,
    gcs: GcsStagingClient,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Fetch locations, friendly names, and per-location readings to GCS.

    A location with no readings in the window (HydroVu answers ``404``) is
    is counted in ``locations_no_data`` and written nothing, not treated as
    an error. Real per-location failures are collected: every other location
    is still staged and the failures are reported in the ``errors`` list.
    """
    dt = end.strftime("%Y-%m-%d")
    prefix = f"raw/pvacd/dt={dt}"
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())

    locations = hydrovu.list_locations()
    objects = [gcs.write_json(f"{prefix}/locations.json", locations)]
    objects.append(
        gcs.write_json(f"{prefix}/friendly_names.json", hydrovu.get_friendly_names())
    )

    errors: list[dict[str, Any]] = []
    no_data: list[int] = []
    readings_written = 0
    for location in locations:
        location_id = location["id"]
        try:
            pages = hydrovu.get_location_data(location_id, start_epoch, end_epoch)
        except HydroVuApiError as exc:
            logger.error("pvacd_ingest location=%s error=%s", location_id, exc)
            errors.append({"location_id": location_id, "error": str(exc)})
            continue
        if not pages:
            logger.info("pvacd_ingest location=%s no data in window", location_id)
            no_data.append(location_id)
            continue
        objects.append(
            gcs.write_json(
                f"{prefix}/readings/location_{location_id}.json",
                {
                    "location_id": location_id,
                    "start_time": start_epoch,
                    "end_time": end_epoch,
                    "pages": pages,
                },
            )
        )
        readings_written += 1

    return {
        "status": "ok",
        "dt": dt,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "locations_count": len(locations),
        "readings_objects_written": readings_written,
        "locations_no_data": len(no_data),
        "objects": objects,
        "errors": errors,
    }


def ingest_failed(result: dict[str, Any]) -> bool:
    """True when real errors prevented any readings from being staged.

    A run where locations merely had no data (no ``errors``) is not a failure.
    """
    return bool(result["errors"]) and result["readings_objects_written"] == 0
