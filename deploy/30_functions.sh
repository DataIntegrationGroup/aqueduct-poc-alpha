#!/usr/bin/env bash
# Service accounts, IAM, HydroVu secrets, and deploy of both Cloud Functions.
#
#   HYDROVU_CLIENT_ID=... HYDROVU_CLIENT_SECRET=... ./deploy/30_functions.sh
#
# Run from the repo root (the deploy uses --source=. for the function bundle).
# Requires 10_infra.sh and 20_frost.sh to have run first.

source "$(dirname "${BASH_SOURCE[0]}")/00_config.sh"
cd "${REPO_ROOT}"

have() { "$@" >/dev/null 2>&1; }
ensure_sa() {
  have gcloud iam service-accounts describe "$(sa_email "$1")" --project="${PROJECT_ID}" \
    || gcloud iam service-accounts create "$1" --project="${PROJECT_ID}" --display-name="$2"
}

echo "== Service accounts =="
ensure_sa "${INGEST_SA}"    "PVACD ingest function"
ensure_sa "${TRANSFORM_SA}" "PVACD transform function"

echo "== HydroVu secrets =="
ensure_secret() {
  local name="$1" val="$2"
  if have gcloud secrets describe "${name}" --project="${PROJECT_ID}"; then
    echo "  ${name} exists — leaving as is"
  elif [[ -n "${val}" ]]; then
    printf '%s' "${val}" | gcloud secrets create "${name}" --project="${PROJECT_ID}" --data-file=-
  else
    echo "ERROR: secret ${name} missing and its env var is unset." >&2
    echo "       Re-run with HYDROVU_CLIENT_ID and HYDROVU_CLIENT_SECRET exported." >&2
    exit 1
  fi
}
ensure_secret "${SECRET_HYDROVU_ID}"     "${HYDROVU_CLIENT_ID:-}"
ensure_secret "${SECRET_HYDROVU_SECRET}" "${HYDROVU_CLIENT_SECRET:-}"

echo "== IAM =="
# Ingest: read the HydroVu secrets, write staged objects to the bucket.
for s in "${SECRET_HYDROVU_ID}" "${SECRET_HYDROVU_SECRET}"; do
  gcloud secrets add-iam-policy-binding "${s}" --project="${PROJECT_ID}" \
    --member="serviceAccount:$(sa_email "${INGEST_SA}")" \
    --role="roles/secretmanager.secretAccessor" --condition=None
done
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET_NAME}" \
  --member="serviceAccount:$(sa_email "${INGEST_SA}")" --role="roles/storage.objectUser"
# Transform: read staged objects only.
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET_NAME}" \
  --member="serviceAccount:$(sa_email "${TRANSFORM_SA}")" --role="roles/storage.objectViewer"

echo "== Deploy ${INGEST_FUNCTION} =="
# Ingest reaches HydroVu (public) + GCS
gcloud functions deploy "${INGEST_FUNCTION}" \
  --project="${PROJECT_ID}" --region="${REGION}" --gen2 \
  --runtime="${RUNTIME}" --source=. --entry-point=pvacd_ingest \
  --trigger-http --no-allow-unauthenticated --timeout=540s \
  --run-service-account="$(sa_email "${INGEST_SA}")" \
  --set-env-vars="GCS_BUCKET_NAME=${GCS_BUCKET_NAME},PVACD_LOOKBACK_DAYS=${PVACD_LOOKBACK_DAYS}" \
  --set-secrets="HYDROVU_CLIENT_ID=${SECRET_HYDROVU_ID}:latest,HYDROVU_CLIENT_SECRET=${SECRET_HYDROVU_SECRET}:latest"

echo "== Deploy ${TRANSFORM_FUNCTION} =="
FROST_SERVICE_ROOT_URL="$(gcloud run services describe "${FROST_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')/FROST-Server/v1.1"
echo "  FROST_SERVICE_ROOT_URL=${FROST_SERVICE_ROOT_URL}"
# Transform must reach FROST's INTERNAL-ingress URL, so route all egress through
# the VPC connector (NAT from 10_infra.sh covers its GCS / Secret Manager calls).
gcloud functions deploy "${TRANSFORM_FUNCTION}" \
  --project="${PROJECT_ID}" --region="${REGION}" --gen2 \
  --runtime="${RUNTIME}" --source=. --entry-point=pvacd_to_frost \
  --trigger-http --no-allow-unauthenticated --timeout=540s \
  --run-service-account="$(sa_email "${TRANSFORM_SA}")" \
  --vpc-connector="${CONNECTOR_NAME}" --egress-settings=all-traffic \
  --set-env-vars="GCS_BUCKET_NAME=${GCS_BUCKET_NAME},FROST_SERVICE_ROOT_URL=${FROST_SERVICE_ROOT_URL}"

cat <<EOF

Functions deployed (both --no-allow-unauthenticated; only the workflow SA may
invoke them — granted in 40_orchestration.sh).

Smoke test the ingest path directly (writes to gs://${GCS_BUCKET_NAME}):
  gcloud functions call ${INGEST_FUNCTION} --region=${REGION} --gen2 --data '{"lookback_days": 1}'

Next: ./deploy/40_orchestration.sh
EOF
