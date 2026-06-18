#!/usr/bin/env bash
# Apply the Cloud Monitoring dashboard and the three alert policies.
#
#   ./deploy/50_monitoring.sh
#
# Re-running creates duplicates (the Monitoring API has no upsert-by-name); see
# the note below to update instead. Notification channels are not attached here
# (see monitoring/README.md).

source "$(dirname "${BASH_SOURCE[0]}")/00_config.sh"
cd "${REPO_ROOT}"

echo "== Dashboard =="
if gcloud monitoring dashboards list --project="${PROJECT_ID}" \
     --format='value(displayName)' | grep -qx "PVACD Pipeline"; then
  echo "  'PVACD Pipeline' dashboard already exists — skipping create."
  echo "  To update: gcloud monitoring dashboards update <ID> --config-from-file=monitoring/dashboard.json"
else
  gcloud monitoring dashboards create --project="${PROJECT_ID}" \
    --config-from-file=monitoring/dashboard.json
fi

echo "== Alert policies =="
for f in monitoring/alerts/*.json; do
  name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["displayName"])' "$f")"
  if gcloud alpha monitoring policies list --project="${PROJECT_ID}" \
       --format='value(displayName)' | grep -qxF "${name}"; then
    echo "  policy exists, skipping: ${name}"
  else
    echo "  creating: ${name}"
    gcloud alpha monitoring policies create --project="${PROJECT_ID}" --policy-from-file="$f"
  fi
done

cat <<EOF

Monitoring applied.
  Dashboard: https://console.cloud.google.com/monitoring/dashboards?project=${PROJECT_ID}
  Alerts:    https://console.cloud.google.com/monitoring/alerting/policies?project=${PROJECT_ID}

Attach a notification channel to be paged — see monitoring/README.md.
The full deploy is done. Verify end-to-end with the runbook in deploy/README.md.
EOF
