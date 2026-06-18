# GCP deploy runbook

Stands up the PVACD pipeline in GCP: ingest -> GCS -> transform -> FROST,
orchestrated by Cloud Workflows on a daily Cloud Scheduler trigger, observed by
Cloud Monitoring. FROST runs in GCP (Cloud Run + Cloud SQL), so the whole
pipeline runs and is monitored in-cloud.

These scripts are are thin wrappers over `gcloud`

## Architecture deployed

```
Cloud Scheduler ─(daily, OAuth)-> Cloud Workflows (pvacd-daily)
                                   │  OIDC
                   ┌───────────────┴───────────────┐
                   v                                v
            pvacd-ingest (CF Gen2) --> GCS --> pvacd-to-frost (CF Gen2)
                   │                                │ VPC connector, all-egress
                   v                                v
              HydroVu API                   FROST (Cloud Run, internal ingress)
                                                    │ VPC connector
                                                    v
                                            Cloud SQL (PostgreSQL + PostGIS, private IP)

Cloud Monitoring observes Workflow executions, both functions (dashboard + 3 alerts)
```

## Prerequisites

- `gcloud` authenticated, with billing enabled on the target project:
  `gcloud auth login && gcloud config set project <PROJECT_ID>`
- `python3` on PATH (scripts use it for JSON wrangling).
- Real HydroVu OAuth credentials (from PVACD's HydroVu account) exported for the
  functions step: `export HYDROVU_CLIENT_ID=... HYDROVU_CLIENT_SECRET=...`
- Owner/Editor rights (the scripts create SAs, IAM bindings, networking,
  Cloud SQL, Cloud Run, functions, workflows, scheduler, monitoring).

## Configure

All knobs live in `00_config.sh` with sane defaults.
Override by exporting before a run. Example:

```bash
export PROJECT_ID=<PROJECT_ID>              # defaults to active gcloud project
export REGION=us-west3
export GCS_BUCKET_NAME=<GCS_BUCKET_NAME>    # defaults to <project>-pvacd-staging
```

## Run order

```bash
./deploy/10_infra.sh          # APIs, VPC + connector + NAT, Cloud SQL, bucket, DB secret
./deploy/20_frost.sh          # FROST-Server on Cloud Run, wired to Cloud SQL
HYDROVU_CLIENT_ID=... HYDROVU_CLIENT_SECRET=... \
  ./deploy/30_functions.sh    # SAs, IAM, HydroVu secrets, deploy both functions
./deploy/40_orchestration.sh  # workflow + scheduler (+ IAM)
./deploy/50_monitoring.sh     # dashboard + 3 alert policies
```

`10_infra.sh` is the slow one (Cloud SQL + VPC peering take several minutes). The
scripts guard each create, so they are safe to re-run if a step fails partway.

## Verify end-to-end

```bash
# 1. FROST is up (run from inside the project, e.g. Cloud Shell)
#    or just confirm the revision is serving:
gcloud run services describe pvacd-frost --region="$REGION" --format='value(status.url)'

# 2. One orchestrated run (1-month backfill), then watch it
gcloud workflows run pvacd-daily --location="$REGION" --data='{"lookback_days": 31}'
gcloud workflows executions list pvacd-daily --location="$REGION"

# 3. Confirm data reached FROST (from inside the VPC / Cloud Shell with access):
#    curl "$FROST_URL/FROST-Server/v1.1/Datastreams?\$expand=Observations(\$top=1)"

# 4. Soak: let the scheduler fire two consecutive days, or force it:
gcloud scheduler jobs run pvacd-daily --location="$REGION"

# 5. Monitoring: open the dashboard + alerts (links printed by 50_monitoring.sh).
#    To exercise the alerts, temporarily break FROST_SERVICE_ROOT_URL on the
#    transform function and run the workflow — sync-failure + 5xx alerts fire.
```

The mocked pytest suite is unaffected by any of this: `uv run pytest -v`.

## Teardown (stop billing)

Cloud SQL, the VPC connector, and `min-instances=1` on FROST bill continuously.
To tear down:

```bash
gcloud scheduler jobs delete pvacd-daily --location="$REGION" -q
gcloud workflows delete pvacd-daily --location="$REGION" -q
gcloud functions delete pvacd-ingest pvacd-to-frost --region="$REGION" --gen2 -q
gcloud run services delete pvacd-frost --region="$REGION" -q
gcloud sql instances delete pvacd-frost-db -q
gcloud compute networks vpc-access connectors delete pvacd-connector --region="$REGION" -q
gcloud compute routers delete pvacd-router --region="$REGION" -q
# VPC peering + reserved range + network last, once nothing else references them.
```

(Monitoring dashboards/policies and Secret Manager secrets are free to leave.)
