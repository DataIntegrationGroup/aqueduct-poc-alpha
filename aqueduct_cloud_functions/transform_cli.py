"""Command-line entry point for the PVACD staging -> FROST transform.

Exposes the same transform as the Cloud Function handler, runnable via ``uv``
without standing up an HTTP server::

    uv run pvacd-to-frost                  # transform today's dt partition
    uv run pvacd-to-frost --dt 2026-06-01  # transform an explicit dt partition

Results are printed as JSON; the process exit code is ``0`` on success, ``1`` on
a transform failure (errors left every datastream unloaded), and ``2`` on a
configuration or argument error.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime

import pydantic

from aqueduct_cloud_functions.pvacd_transform import (
    build_clients,
    run_pvacd_to_frost,
    transform_failed,
)
from aqueduct_cloud_functions.settings import FrostLoadSettings

logger = logging.getLogger(__name__)

_EXIT_OK = 0
_EXIT_TRANSFORM_FAILURE = 1
_EXIT_CONFIG_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the transform CLI."""
    parser = argparse.ArgumentParser(
        prog="pvacd-to-frost",
        description="Transform a staged PVACD dt partition into FROST.",
    )
    parser.add_argument(
        "--dt",
        default=None,
        help="Staging partition to transform, YYYY-MM-DD (default: today UTC).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Resolve the dt partition, run the transform, and print the result."""
    args = _build_parser().parse_args(argv)

    try:
        settings = FrostLoadSettings()
    except pydantic.ValidationError as exc:
        logger.error("pvacd-to-frost missing configuration: %s", exc)
        print(
            json.dumps({"status": "error", "message": f"missing configuration: {exc}"})
        )
        return _EXIT_CONFIG_ERROR

    dt = args.dt or datetime.now(tz=UTC).strftime("%Y-%m-%d")
    try:
        datetime.strptime(dt, "%Y-%m-%d")  # validate format
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return _EXIT_CONFIG_ERROR

    gcs, frost = build_clients(settings)
    try:
        result = run_pvacd_to_frost(gcs, frost, dt)
    except Exception as exc:  # noqa: BLE001 - surface GCS/FROST failures as exit 1
        logger.error("pvacd-to-frost transform failure: %s", exc)
        print(json.dumps({"status": "error", "message": str(exc)}))
        return _EXIT_TRANSFORM_FAILURE
    finally:
        frost.close()

    if transform_failed(result):
        result = {**result, "status": "error"}

    print(json.dumps(result, indent=2))
    return _EXIT_OK if result["status"] == "ok" else _EXIT_TRANSFORM_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
