"""Unit tests for GcsStagingClient using a mocked storage SDK client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from aqueduct_cloud_functions.clients import GcsStagingClient


def test_write_json_uploads_and_returns_uri() -> None:
    """write_json serializes the payload, sets content type, returns gs:// URI."""
    storage_client = MagicMock()
    client = GcsStagingClient(bucket_name="test-bucket", client=storage_client)

    payload = {"a": 1, "items": [1, 2]}
    uri = client.write_json("raw/pvacd/dt=2026-06-09/locations.json", payload)

    assert uri == "gs://test-bucket/raw/pvacd/dt=2026-06-09/locations.json"
    storage_client.bucket.assert_called_once_with("test-bucket")
    bucket = storage_client.bucket.return_value
    bucket.blob.assert_called_once_with("raw/pvacd/dt=2026-06-09/locations.json")
    blob = bucket.blob.return_value
    blob.upload_from_string.assert_called_once_with(
        json.dumps(payload), content_type="application/json"
    )
