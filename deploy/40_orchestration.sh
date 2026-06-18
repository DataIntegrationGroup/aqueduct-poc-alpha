#!/usr/bin/env bash
# Deploy the Cloud Workflow and the Cloud Scheduler job that drives it daily.
#
#   ./deploy/40_orchestration.sh
#
# Requires the functions from 30_functions.sh.

source "$(dirname "${BASH_SOURCE[0]}")/00_config.sh"
cd "${REPO_ROOT}"

have() { "$@" >/dev/null 2>&1; }
ensure_sa() {
  have gcloud iam service-accounts describe "$(sa_email "$1")" --project="${PROJECT_ID}" \
    || gcloud iam service-accounts create "$1" --project="${PROJECT_ID}" --display-name="$2"
}

echo "== Service accounts =="
ensure_sa "${WORKFLOW_SA}"  "PVACD workflow runtime"
ensure_sa "${SCHEDULER_SA}" "PVACD scheduler"

echo "== IAM: workflow SA may invoke both functions =="
for fn in "${INGEST_FUNCTION}" "${TRANSFORM_FUNCTION}"; do
  gcloud run services add-iam-policy-binding "${fn}" \
    --project="${PROJECT_ID}" --region="${REGION}" \
    --member="serviceAccount:$(sa_email "${WORKFLOW_SA}")" --role="roles/run.invoker"
done

echo "== IAM: scheduler SA may start workflow executions =="
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:$(sa_email "${SCHEDULER_SA}")" \
  --role="roles/workflows.invoker" --condition=None >/dev/null

echo "== Deploy workflow ${WORKFLOW_NAME} =="
gcloud workflows deploy "${WORKFLOW_NAME}" \
  --project="${PROJECT_ID}" --location="${REGION}" \
  --source=workflows/pvacd_daily.yaml \
  --service-account="$(sa_email "${WORKFLOW_SA}")"

echo "== Cloud Scheduler job =="
# Pass the real function URLs into the execution argument so the run is robust
# regardless of cloudfunctions.net aliasing (the workflow's own URL construction
# stays a fallback for manual `gcloud workflows run` with no args).
INGEST_URL="$(gcloud functions describe "${INGEST_FUNCTION}" --gen2 --region="${REGION}" --project="${PROJECT_ID}" --format='value(url)')"
TRANSFORM_URL="$(gcloud functions describe "${TRANSFORM_FUNCTION}" --gen2 --region="${REGION}" --project="${PROJECT_ID}" --format='value(url)')"
ARG_JSON="$(python3 -c 'import json,sys; print(json.dumps({"lookback_days": int(sys.argv[1]), "ingest_url": sys.argv[2], "transform_url": sys.argv[3]}))' \
  "${PVACD_LOOKBACK_DAYS}" "${INGEST_URL}" "${TRANSFORM_URL}")"
# The Workflows executions API takes {"argument": "<json string>"}.
BODY="$(python3 -c 'import json,sys; print(json.dumps({"argument": sys.argv[1], "callLogLevel": "LOG_ERRORS_ONLY"}))' "${ARG_JSON}")"
URI="https://workflowexecutions.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/workflows/${WORKFLOW_NAME}/executions"

SCHED_ACTION=create
have gcloud scheduler jobs describe "${SCHEDULER_JOB}" --project="${PROJECT_ID}" --location="${REGION}" && SCHED_ACTION=update
gcloud scheduler jobs "${SCHED_ACTION}" http "${SCHEDULER_JOB}" \
  --project="${PROJECT_ID}" --location="${REGION}" \
  --schedule="${SCHEDULE}" --time-zone="${SCHEDULE_TZ}" \
  --uri="${URI}" --http-method=POST \
  --oauth-service-account-email="$(sa_email "${SCHEDULER_SA}")" \
  --message-body="${BODY}"

cat <<EOF

Orchestration deployed.
  Run once now (backfill 1 month):
    gcloud workflows run ${WORKFLOW_NAME} --location=${REGION} --data='{"lookback_days": 31}'
  Force the scheduled path immediately:
    gcloud scheduler jobs run ${SCHEDULER_JOB} --location=${REGION}
  Watch executions:
    gcloud workflows executions list ${WORKFLOW_NAME} --location=${REGION}

Next: ./deploy/50_monitoring.sh
EOF
