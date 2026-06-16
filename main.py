"""Cloud Functions Gen 2 HTTP handlers — one deploy bundle, multiple entry points."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import functions_framework
import pydantic
from flask import Request

from aqueduct_cloud_functions.clients import (
    HydroVuApiError,
    HydroVuAuthError,
)
from aqueduct_cloud_functions.pvacd import (
    build_clients,
    ingest_failed,
    resolve_window,
    run_pvacd_ingest,
)
from aqueduct_cloud_functions.pvacd_transform import (
    build_clients as build_transform_clients,
)
from aqueduct_cloud_functions.pvacd_transform import (
    run_pvacd_to_frost,
    transform_failed,
)
from aqueduct_cloud_functions.settings import FrostLoadSettings, PvacdIngestSettings

logger = logging.getLogger(__name__)


def _parse_window(
    request: Request, default_lookback_days: int
) -> tuple[datetime, datetime]:
    """Resolve the [start, end) UTC window from request params.

    Merges ``start_date``/``end_date``/``lookback_days`` from the query string
    and JSON body (body wins), then delegates to
    :func:`aqueduct_cloud_functions.pvacd.resolve_window`.
    """
    params: dict[str, Any] = dict(request.args)
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        params.update(body)
    return resolve_window(
        start_date=params.get("start_date"),
        end_date=params.get("end_date"),
        lookback_days=params.get("lookback_days"),
        default_lookback_days=default_lookback_days,
    )


@functions_framework.http
def pvacd_ingest(request: Request) -> tuple[Any, int]:
    """PVACD HydroVu ingest -> GCS staging."""
    try:
        settings = PvacdIngestSettings()
    except pydantic.ValidationError as exc:
        logger.error("pvacd_ingest missing configuration: %s", exc)
        return ({"status": "error", "message": f"missing configuration: {exc}"}, 500)

    try:
        start, end = _parse_window(request, settings.pvacd_lookback_days)
    except ValueError as exc:
        return ({"status": "error", "message": str(exc)}, 400)

    hydrovu, gcs = build_clients(settings)
    try:
        result = run_pvacd_ingest(hydrovu, gcs, start, end)
    except (HydroVuAuthError, HydroVuApiError) as exc:
        logger.error("pvacd_ingest upstream failure: %s", exc)
        return ({"status": "error", "message": str(exc)}, 502)
    finally:
        hydrovu.close()

    if ingest_failed(result):
        return ({**result, "status": "error"}, 502)
    return (result, 200)


@functions_framework.http
def cabq_ingest(request: Request) -> tuple[Any, int]:
    """CABQ CKAN ingest → GCS staging."""
    # TODO: implement
    return ("", 501)


def _parse_dt(request: Request) -> str:
    """Resolve the ``dt`` partition (``YYYY-MM-DD``) to transform.

    Reads ``dt`` from the query string or JSON body; defaults to
    today (UTC). Raises ValueError on a malformed value.
    """
    params: dict[str, Any] = dict(request.args)
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        params.update(body)
    dt = params.get("dt")
    if dt is None:
        return datetime.now(tz=UTC).strftime("%Y-%m-%d")
    datetime.strptime(str(dt), "%Y-%m-%d")  # validate format
    return str(dt)


@functions_framework.http
def pvacd_to_frost(request: Request) -> tuple[Any, int]:
    """PVACD staged GCS data → canonical model → FROST."""
    try:
        settings = FrostLoadSettings()
    except pydantic.ValidationError as exc:
        logger.error("pvacd_to_frost missing configuration: %s", exc)
        return ({"status": "error", "message": f"missing configuration: {exc}"}, 500)

    try:
        dt = _parse_dt(request)
    except ValueError as exc:
        return ({"status": "error", "message": str(exc)}, 400)

    gcs, frost = build_transform_clients(settings)
    try:
        result = run_pvacd_to_frost(gcs, frost, dt)
    except Exception as exc:  # noqa: BLE001 - surface GCS/FROST failures as 502
        logger.error("pvacd_to_frost transform failure: %s", exc)
        return ({"status": "error", "message": str(exc)}, 502)
    finally:
        frost.close()

    if transform_failed(result):
        return ({**result, "status": "error"}, 502)
    return (result, 200)


@functions_framework.http
def cabq_to_frost(request: Request) -> tuple[Any, int]:
    """CABQ staged GCS data → canonical model → FROST."""
    # TODO: implement (lazy-import adapter inside handler when ready)
    # from aqueduct_cloud_functions.adapters import CabqAdapter
    return ("", 501)
