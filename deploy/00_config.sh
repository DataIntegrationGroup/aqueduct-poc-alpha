#!/usr/bin/env bash
# Shared configuration for the PVACD GCP deploy scripts.
#
# Sourced by every other deploy/*.sh. Override any value by exporting it before
# you run a script, e.g.:
#   PROJECT_ID=my-proj REGION=us-west1 ./deploy/10_infra.sh
#
# Nothing here calls GCP, it only sets variables.

set -euo pipefail

# ── Project / location ───────────────────────────────────────────────────────
# Falls back to your active gcloud project if PROJECT_ID is unset.
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: PROJECT_ID is not set and no active gcloud project found." >&2
  echo "       Run: gcloud config set project <PROJECT_ID>" >&2
  exit 1
fi

# ── Staging bucket (where ingest lands raw JSON) ─────────────────────────────
GCS_BUCKET_NAME="${GCS_BUCKET_NAME:-${PROJECT_ID}-pvacd-staging}"

# ── Networking ───────────────────────────────────────────────────────────────
VPC_NAME="${VPC_NAME:-pvacd-vpc}"
SUBNET_NAME="${SUBNET_NAME:-pvacd-subnet}"
SUBNET_RANGE="${SUBNET_RANGE:-10.10.0.0/24}"
CONNECTOR_NAME="${CONNECTOR_NAME:-pvacd-connector}"
CONNECTOR_RANGE="${CONNECTOR_RANGE:-10.8.0.0/28}"   # /28, must not overlap others
ROUTER_NAME="${ROUTER_NAME:-pvacd-router}"
NAT_NAME="${NAT_NAME:-pvacd-nat}"
PSA_RANGE_NAME="${PSA_RANGE_NAME:-pvacd-psa-range}" # private services access (Cloud SQL)

# ── Cloud SQL (PostGIS for FROST) ────────────────────────────────────────────
SQL_INSTANCE="${SQL_INSTANCE:-pvacd-frost-db}"
SQL_TIER="${SQL_TIER:-db-custom-1-3840}"            # 1 vCPU / 3.75 GB; smallest custom
SQL_DB="${SQL_DB:-sensorthings}"
SQL_USER="${SQL_USER:-sensorthings}"

# ── FROST on Cloud Run ───────────────────────────────────────────────────────
FROST_SERVICE="${FROST_SERVICE:-pvacd-frost}"
FROST_IMAGE="${FROST_IMAGE:-fraunhoferiosb/frost-server:2.6}"

# ── Cloud Functions ──────────────────────────────────────────────────────────
INGEST_FUNCTION="${INGEST_FUNCTION:-pvacd-ingest}"
TRANSFORM_FUNCTION="${TRANSFORM_FUNCTION:-pvacd-to-frost}"
RUNTIME="${RUNTIME:-python313}"
PVACD_LOOKBACK_DAYS="${PVACD_LOOKBACK_DAYS:-1}"

# ── Orchestration ────────────────────────────────────────────────────────────
WORKFLOW_NAME="${WORKFLOW_NAME:-pvacd-daily}"
SCHEDULER_JOB="${SCHEDULER_JOB:-pvacd-daily}"
SCHEDULE="${SCHEDULE:-0 8 * * *}"                   # 08:00 daily
SCHEDULE_TZ="${SCHEDULE_TZ:-America/Denver}"

# ── Service accounts (created by the scripts) ────────────────────────────────
INGEST_SA="${INGEST_SA:-pvacd-ingest-sa}"
TRANSFORM_SA="${TRANSFORM_SA:-pvacd-transform-sa}"
WORKFLOW_SA="${WORKFLOW_SA:-pvacd-workflow-sa}"
SCHEDULER_SA="${SCHEDULER_SA:-pvacd-scheduler-sa}"
sa_email() { echo "${1}@${PROJECT_ID}.iam.gserviceaccount.com"; }

# ── Secret Manager secret names ──────────────────────────────────────────────
SECRET_HYDROVU_ID="${SECRET_HYDROVU_ID:-hydrovu-client-id}"
SECRET_HYDROVU_SECRET="${SECRET_HYDROVU_SECRET:-hydrovu-client-secret}"
SECRET_FROST_DB_PW="${SECRET_FROST_DB_PW:-frost-db-password}"

# Repo root, so scripts work from any CWD.
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

echo "Config: project=${PROJECT_ID} region=${REGION} bucket=${GCS_BUCKET_NAME}"
