"""Environment-driven configuration for the Cloud Function handlers.

Values come from environment variables (or a local ``.env`` file for
development). In deployed Cloud Functions the secret values are injected
as env vars via ``gcloud functions deploy --set-secrets``.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class PvacdIngestSettings(BaseSettings):
    """Configuration for the ``pvacd_ingest`` Cloud Function."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hydrovu_client_id: str
    hydrovu_client_secret: str
    hydrovu_token_url: str = "https://www.hydrovu.com/public-api/oauth/token"
    hydrovu_api_base_url: str = "https://www.hydrovu.com/public-api/v1"
    gcs_bucket_name: str
    pvacd_lookback_days: int = 1
