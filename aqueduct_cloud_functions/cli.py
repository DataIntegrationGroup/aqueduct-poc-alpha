"""Command-line entry point for running the PVACD HydroVu ingest.

Exposes the same ingest as the Cloud Function handler, runnable via ``uv``
without standing up an HTTP server. Each subcommand maps to a load type::

    uv run pvacd-ingest daily                                  # incremental
    uv run pvacd-ingest backfill --days 31                     # 1-month backfill
    uv run pvacd-ingest range --start 2026-05-01 --end 2026-06-01

All modes share the same code path
(:func:`aqueduct_cloud_functions.pvacd.run_pvacd_ingest`); they differ only by
the resolved time window. Results are printed as JSON; the process exit code is
``0`` on success, ``1`` on an upstream/ingest failure, and ``2`` on a
configuration or window error.
"""

from __future__ import annotations

import argparse
import json
import logging

import pydantic

from aqueduct_cloud_functions.clients import HydroVuApiError, HydroVuAuthError
from aqueduct_cloud_functions.pvacd import (
    build_clients,
    resolve_window,
    run_pvacd_ingest,
)
from aqueduct_cloud_functions.settings import PvacdIngestSettings

logger = logging.getLogger(__name__)

_EXIT_OK = 0
_EXIT_INGEST_FAILURE = 1
_EXIT_CONFIG_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subcommand per load type."""
    parser = argparse.ArgumentParser(
        prog="pvacd-ingest",
        description="Run the PVACD HydroVu → GCS ingest for a chosen time window.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser(
        "daily", help="Incremental load (defaults to PVACD_LOOKBACK_DAYS)."
    )
    daily.add_argument(
        "--days",
        type=int,
        default=None,
        help="Lookback window in days (default: PVACD_LOOKBACK_DAYS).",
    )

    backfill = subparsers.add_parser(
        "backfill", help="Wide historical load ending now (one dt=<today> partition)."
    )
    backfill.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback window in days (default: 30).",
    )

    window = subparsers.add_parser(
        "range", help="Explicit [start, end) window (YYYY-MM-DD)."
    )
    window.add_argument("--start", required=True, help="Window start, YYYY-MM-DD.")
    window.add_argument("--end", required=True, help="Window end / dt, YYYY-MM-DD.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Resolve the window from CLI args, run the ingest, and print the result."""
    args = _build_parser().parse_args(argv)

    try:
        settings = PvacdIngestSettings()
    except pydantic.ValidationError as exc:
        logger.error("pvacd-ingest missing configuration: %s", exc)
        print(
            json.dumps({"status": "error", "message": f"missing configuration: {exc}"})
        )
        return _EXIT_CONFIG_ERROR

    start_date = getattr(args, "start", None)
    end_date = getattr(args, "end", None)
    lookback_days = getattr(args, "days", None)
    try:
        start, end = resolve_window(
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            default_lookback_days=settings.pvacd_lookback_days,
        )
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return _EXIT_CONFIG_ERROR

    hydrovu, gcs = build_clients(settings)
    try:
        result = run_pvacd_ingest(hydrovu, gcs, start, end)
    except (HydroVuAuthError, HydroVuApiError) as exc:
        logger.error("pvacd-ingest upstream failure: %s", exc)
        print(json.dumps({"status": "error", "message": str(exc)}))
        return _EXIT_INGEST_FAILURE
    finally:
        hydrovu.close()

    if result["locations_count"] > 0 and result["readings_objects_written"] == 0:
        result = {**result, "status": "error"}

    print(json.dumps(result, indent=2))
    return _EXIT_OK if result["status"] == "ok" else _EXIT_INGEST_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
