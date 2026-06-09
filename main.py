"""Cloud Functions Gen 2 HTTP handlers — one deploy bundle, multiple entry points."""

import functions_framework
from flask import Request


@functions_framework.http
def pvacd_ingest(request: Request):
    """PVACD HydroVu ingest → GCS staging."""
    # TODO: implement
    return ("", 501)


@functions_framework.http
def cabq_ingest(request: Request):
    """CABQ CKAN ingest → GCS staging."""
    # TODO: implement
    return ("", 501)


@functions_framework.http
def pvacd_to_frost(request: Request):
    """PVACD staged GCS data → canonical model → FROST."""
    # TODO: implement (lazy-import adapter inside handler when ready)
    # from aqueduct_cloud_functions.adapters import HydroVuAdapter
    return ("", 501)


@functions_framework.http
def cabq_to_frost(request: Request):
    """CABQ staged GCS data → canonical model → FROST."""
    # TODO: implement (lazy-import adapter inside handler when ready)
    # from aqueduct_cloud_functions.adapters import CabqAdapter
    return ("", 501)
