#!/usr/bin/env bash
# Stand up the shared infrastructure: APIs, VPC + connector + Cloud NAT,
# private Cloud SQL (PostGIS), the staging bucket, and the DB-password secret.
#
# Each create is guarded so re-running skips what already exists.
#
#   ./deploy/10_infra.sh
#
# Billable: Cloud SQL and the VPC connector run continuously. Cloud SQL is the
# main cost and the slowest to delete (see deploy/README.md "Teardown").

source "$(dirname "${BASH_SOURCE[0]}")/00_config.sh"

have() { "$@" >/dev/null 2>&1; }

echo "== Enabling APIs =="
gcloud services enable \
  cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  sqladmin.googleapis.com servicenetworking.googleapis.com \
  vpcaccess.googleapis.com compute.googleapis.com \
  workflows.googleapis.com workflowexecutions.googleapis.com \
  cloudscheduler.googleapis.com monitoring.googleapis.com \
  --project="${PROJECT_ID}"

echo "== Staging bucket =="
if ! have gcloud storage buckets describe "gs://${GCS_BUCKET_NAME}"; then
  gcloud storage buckets create "gs://${GCS_BUCKET_NAME}" \
    --project="${PROJECT_ID}" --location="${REGION}" \
    --uniform-bucket-level-access
fi

echo "== VPC + subnet =="
if ! have gcloud compute networks describe "${VPC_NAME}" --project="${PROJECT_ID}"; then
  gcloud compute networks create "${VPC_NAME}" \
    --project="${PROJECT_ID}" --subnet-mode=custom
fi
if ! have gcloud compute networks subnets describe "${SUBNET_NAME}" \
      --region="${REGION}" --project="${PROJECT_ID}"; then
  gcloud compute networks subnets create "${SUBNET_NAME}" \
    --project="${PROJECT_ID}" --network="${VPC_NAME}" --region="${REGION}" \
    --range="${SUBNET_RANGE}" --enable-private-ip-google-access
fi

echo "== Serverless VPC Access connector =="
# The transform function egresses through this to reach FROST (internal ingress).
if ! have gcloud compute networks vpc-access connectors describe "${CONNECTOR_NAME}" \
      --region="${REGION}" --project="${PROJECT_ID}"; then
  gcloud compute networks vpc-access connectors create "${CONNECTOR_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" --network="${VPC_NAME}" \
    --range="${CONNECTOR_RANGE}"
fi

echo "== Cloud Router + Cloud NAT =="
# With the transform function set to route ALL egress through the VPC (so its
# call to FROST is seen as internal), NAT gives it outbound access to the public
# Google APIs (GCS, Secret Manager) and HydroVu it still needs.
if ! have gcloud compute routers describe "${ROUTER_NAME}" \
      --region="${REGION}" --project="${PROJECT_ID}"; then
  gcloud compute routers create "${ROUTER_NAME}" \
    --project="${PROJECT_ID}" --network="${VPC_NAME}" --region="${REGION}"
fi
if ! have gcloud compute routers nats describe "${NAT_NAME}" \
      --router="${ROUTER_NAME}" --region="${REGION}" --project="${PROJECT_ID}"; then
  gcloud compute routers nats create "${NAT_NAME}" \
    --project="${PROJECT_ID}" --router="${ROUTER_NAME}" --region="${REGION}" \
    --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges
fi

echo "== Private Service Access (for Cloud SQL private IP) =="
if ! have gcloud compute addresses describe "${PSA_RANGE_NAME}" \
      --global --project="${PROJECT_ID}"; then
  gcloud compute addresses create "${PSA_RANGE_NAME}" \
    --project="${PROJECT_ID}" --global --purpose=VPC_PEERING \
    --prefix-length=16 --network="${VPC_NAME}"
fi
# Safe to re-run; no-ops if the peering already exists.
gcloud services vpc-peerings connect \
  --project="${PROJECT_ID}" --service=servicenetworking.googleapis.com \
  --ranges="${PSA_RANGE_NAME}" --network="${VPC_NAME}" || true

echo "== Cloud SQL (PostgreSQL 16, private IP) =="
if ! have gcloud sql instances describe "${SQL_INSTANCE}" --project="${PROJECT_ID}"; then
  gcloud sql instances create "${SQL_INSTANCE}" \
    --project="${PROJECT_ID}" --database-version=POSTGRES_16 \
    --region="${REGION}" --tier="${SQL_TIER}" --edition=ENTERPRISE \
    --no-assign-ip --network="projects/${PROJECT_ID}/global/networks/${VPC_NAME}"
fi

echo "== DB password secret =="
if ! have gcloud secrets describe "${SECRET_FROST_DB_PW}" --project="${PROJECT_ID}"; then
  # Generate a strong password once and store it; FROST reads it from here.
  DB_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  printf '%s' "${DB_PW}" | gcloud secrets create "${SECRET_FROST_DB_PW}" \
    --project="${PROJECT_ID}" --data-file=-
else
  DB_PW="$(gcloud secrets versions access latest --secret="${SECRET_FROST_DB_PW}" --project="${PROJECT_ID}")"
fi

echo "== Database + user =="
have gcloud sql databases describe "${SQL_DB}" --instance="${SQL_INSTANCE}" --project="${PROJECT_ID}" \
  || gcloud sql databases create "${SQL_DB}" --instance="${SQL_INSTANCE}" --project="${PROJECT_ID}"
# create-or-update the app user's password to match the secret
if gcloud sql users list --instance="${SQL_INSTANCE}" --project="${PROJECT_ID}" \
      --format='value(name)' | grep -qx "${SQL_USER}"; then
  gcloud sql users set-password "${SQL_USER}" --instance="${SQL_INSTANCE}" \
    --project="${PROJECT_ID}" --password="${DB_PW}"
else
  gcloud sql users create "${SQL_USER}" --instance="${SQL_INSTANCE}" \
    --project="${PROJECT_ID}" --password="${DB_PW}"
fi

cat <<EOF

Infra ready.
  Cloud SQL private IP: $(gcloud sql instances describe "${SQL_INSTANCE}" --project="${PROJECT_ID}" --format='value(ipAddresses[0].ipAddress)')

PostGIS note: FROST creates its schema on first boot (persistence_autoUpdateDatabase),
including 'CREATE EXTENSION IF NOT EXISTS postgis'. The '${SQL_USER}' user is a
member of cloudsqlsuperuser and may create it. If FROST logs an extension error,
run once:  gcloud sql connect ${SQL_INSTANCE} --user=${SQL_USER} --database=${SQL_DB}
then:      CREATE EXTENSION IF NOT EXISTS postgis;

Next: ./deploy/20_frost.sh
EOF
