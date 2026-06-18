"""Thin wrapper for writing raw staging objects to the GCS landing bucket."""

from __future__ import annotations

import json
from typing import Any

import google.cloud.storage as storage


class GcsStagingClient:
    """Writes raw JSON staging objects to a GCS bucket."""

    def __init__(self, bucket_name: str, client: storage.Client | None = None) -> None:
        """Bind to a bucket; an injected client keeps unit tests SDK-free."""
        self._bucket_name = bucket_name
        self._client = client or storage.Client()

    def write_json(self, object_path: str, payload: Any) -> str:
        """Serialize ``payload`` as JSON, upload it, and return the gs:// URI."""
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(object_path)
        blob.upload_from_string(json.dumps(payload), content_type="application/json")
        return f"gs://{self._bucket_name}/{object_path}"

    def read_json(self, object_path: str) -> Any:
        """Download ``object_path`` and parse it as JSON."""
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(object_path)
        return json.loads(blob.download_as_bytes())

    def list_objects(self, prefix: str) -> list[str]:
        """List object names under ``prefix`` (e.g. a ``readings/`` partition)."""
        return [
            blob.name
            for blob in self._client.list_blobs(self._bucket_name, prefix=prefix)
        ]
