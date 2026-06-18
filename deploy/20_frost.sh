#!/usr/bin/env bash
# Deploy FROST-Server on Cloud Run, wired to the private Cloud SQL instance.
#
#   ./deploy/20_frost.sh
#
# Security model: ingress = internal (only reachable from the VPC) + IAM
# allow-unauthenticated. The transform's FrostLoader sends no auth header
# (frost_loader.py), so we isolate FROST at the NETWORK layer rather than with
# IAM tokens: the public internet cannot reach it, but in-VPC callers need no
# token. The connector lets FROST reach Cloud SQL's private IP.

source "$(dirname "${BASH_SOURCE[0]}")/00_config.sh"

# The instance was created --no-assign-ip, so its only address is the private one.
SQL_PRIVATE_IP="$(gcloud sql instances describe "${SQL_INSTANCE}" \
  --project="${PROJECT_ID}" --format='value(ipAddresses[0].ipAddress)')"
echo "Cloud SQL private IP: ${SQL_PRIVATE_IP}"

# '@'-delimited so values containing ':' and '/' (the JDBC URL) survive parsing.
ENV_VARS="^@^persistence_db_driver=org.postgresql.Driver"
ENV_VARS="${ENV_VARS}@persistence_db_url=jdbc:postgresql://${SQL_PRIVATE_IP}:5432/${SQL_DB}"
ENV_VARS="${ENV_VARS}@persistence_db_username=${SQL_USER}"
ENV_VARS="${ENV_VARS}@persistence_autoUpdateDatabase=true"
ENV_VARS="${ENV_VARS}@plugins_modelLoader_enable=true"
ENV_VARS="${ENV_VARS}@plugins_multiDatastream_enable=false"
ENV_VARS="${ENV_VARS}@plugins_actuation_enable=false"
ENV_VARS="${ENV_VARS}@http_cors_enable=true"
ENV_VARS="${ENV_VARS}@http_cors_allowed_origins=*"

echo "== Deploy FROST (pass 1: bring it up) =="
gcloud run deploy "${FROST_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --image="${FROST_IMAGE}" --port=8080 \
  --ingress=internal --allow-unauthenticated \
  --vpc-connector="${CONNECTOR_NAME}" --vpc-egress=private-ranges-only \
  --min-instances=1 --memory=1Gi --cpu=1 --timeout=300 \
  --set-secrets="persistence_db_password=${SECRET_FROST_DB_PW}:latest" \
  --set-env-vars="${ENV_VARS}"

FROST_URL="$(gcloud run services describe "${FROST_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
echo "FROST URL: ${FROST_URL}"

echo "== Deploy FROST (pass 2: set serviceRootUrl to its own URL) =="
# FROST needs its own external root for the self-links in responses.
gcloud run services update "${FROST_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --update-env-vars="serviceRootUrl=${FROST_URL}/FROST-Server"

cat <<EOF

FROST deployed.
  Service root : ${FROST_URL}/FROST-Server
  v1.1 API     : ${FROST_URL}/FROST-Server/v1.1
  (Reachable only from inside ${VPC_NAME} — the transform function uses it.)

The transform deploy (30_functions.sh) reads FROST_SERVICE_ROOT_URL from the
running service automatically, so no action needed. For reference:
  FROST_SERVICE_ROOT_URL=${FROST_URL}/FROST-Server/v1.1

Next: ./deploy/30_functions.sh
EOF
