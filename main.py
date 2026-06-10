"""Cloud Functions Gen 2 HTTP handlers — one deploy bundle, multiple entry points."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import functions_framework
import pydantic
from flask import Request

from aqueduct_cloud_functions.clients import (
    GcsStagingClient,
    HydroVuApiError,
    HydroVuAuthError,
    HydroVuClient,
)
from aqueduct_cloud_functions.settings import PvacdIngestSettings

logger = logging.getLogger(__name__)


def _build_clients(
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


def _parse_window(
    request: Request, default_lookback_days: int
) -> tuple[datetime, datetime]:
    """Resolve the [start, end) UTC window from request params.

    Accepts ``start_date``/``end_date`` (YYYY-MM-DD) or ``lookback_days``
    (int) from the query string or JSON body; body values win. Defaults to
    ``end = now`` and ``start = end - default_lookback_days``.

    Raises ValueError on malformed values or an empty/negative window.
    """
    params: dict[str, Any] = dict(request.args)
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        params.update(body)

    end = datetime.now(tz=UTC)
    if "end_date" in params:
        end = datetime.strptime(str(params["end_date"]), "%Y-%m-%d").replace(tzinfo=UTC)

    if "start_date" in params:
        start = datetime.strptime(str(params["start_date"]), "%Y-%m-%d").replace(
            tzinfo=UTC
        )
    else:
        lookback_days = int(params.get("lookback_days", default_lookback_days))
        if lookback_days <= 0:
            raise ValueError("lookback_days must be a positive integer")
        start = end - timedelta(days=lookback_days)

    if start >= end:
        raise ValueError("start_date must be before end_date")
    return start, end


def _run_pvacd_ingest(
    hydrovu: HydroVuClient,
    gcs: GcsStagingClient,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Fetch locations, friendly names, and per-location readings to GCS.

    Location failures are collected, not fatal: every reachable location is
    still staged and the failures are reported in the ``errors`` list.
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
    readings_written = 0
    for location in locations:
        location_id = location["id"]
        try:
            pages = hydrovu.get_location_data(location_id, start_epoch, end_epoch)
        except HydroVuApiError as exc:
            logger.error("pvacd_ingest location=%s error=%s", location_id, exc)
            errors.append({"location_id": location_id, "error": str(exc)})
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
        "objects": objects,
        "errors": errors,
    }


@functions_framework.http
def pvacd_ingest(request: Request) -> tuple[Any, int]:
    """PVACD HydroVu ingest → GCS staging."""
    try:
        settings = PvacdIngestSettings()
    except pydantic.ValidationError as exc:
        logger.error("pvacd_ingest missing configuration: %s", exc)
        return ({"status": "error", "message": f"missing configuration: {exc}"}, 500)

    try:
        start, end = _parse_window(request, settings.pvacd_lookback_days)
    except ValueError as exc:
        return ({"status": "error", "message": str(exc)}, 400)

    hydrovu, gcs = _build_clients(settings)
    try:
        result = _run_pvacd_ingest(hydrovu, gcs, start, end)
    except (HydroVuAuthError, HydroVuApiError) as exc:
        logger.error("pvacd_ingest upstream failure: %s", exc)
        return ({"status": "error", "message": str(exc)}, 502)
    finally:
        hydrovu.close()

    if result["locations_count"] > 0 and result["readings_objects_written"] == 0:
        return ({**result, "status": "error"}, 502)
    return (result, 200)


@functions_framework.http
def cabq_ingest(request: Request) -> tuple[Any, int]:
    """CABQ CKAN ingest → GCS staging."""
    # TODO: implement
    return ("", 501)


@functions_framework.http
def pvacd_to_frost(request: Request) -> tuple[Any, int]:
    """PVACD staged GCS data → canonical model → FROST."""
    # TODO: implement (lazy-import adapter inside handler when ready)
    # from aqueduct_cloud_functions.adapters import HydroVuAdapter
    return ("", 501)


@functions_framework.http
def cabq_to_frost(request: Request) -> tuple[Any, int]:
    """CABQ staged GCS data → canonical model → FROST."""
    # TODO: implement (lazy-import adapter inside handler when ready)
    # from aqueduct_cloud_functions.adapters import CabqAdapter
    return ("", 501)
