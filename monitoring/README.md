# Cloud Monitoring config

Committed, reproducible Cloud Monitoring for the PVACD pipeline.

| File | Resource | Signal |
| --- | --- | --- |
| `dashboard.json` | Monitoring dashboard | run outcomes, function traffic, latency |
| `alerts/sync_failure.json` | Alert policy | a `pvacd-daily` execution finished `FAILED` |
| `alerts/freshness.json` | Alert policy | no `SUCCEEDED` execution in 24h (staleness) |
| `alerts/error_rate.json` | Alert policy | a `pvacd-*` function returned a `5xx` |

All three alerts map to the README's required example alerts (sync failure,
freshness, error). They key off standard GCP metrics:
`workflows.googleapis.com/finished_execution_count` and
`run.googleapis.com/request_count`.

## Apply

Applied using a deployment script `deploy/40_scheduler_monitoring.sh`, or manually:

```bash
# Dashboard
gcloud monitoring dashboards create --config-from-file=monitoring/dashboard.json

# Alert policies
for f in monitoring/alerts/*.json; do
  gcloud alpha monitoring policies create --policy-from-file="$f"
done
```

### Notification channels

The policy files carry no `notificationChannels`.
To actually be paged, create a channel once and attach it:

```bash
gcloud beta monitoring channels create \
  --display-name="PVACD oncall" --type=email \
  --channel-labels=email_address=oncall@example.com
# then, per policy:
gcloud alpha monitoring policies update POLICY_ID \
  --add-notification-channels=CHANNEL_ID
```

Without a channel the policies still open/close incidents in the console

## Per-partition 'freshness' via a log-based metric (optional)

`freshness.json` answers "did a run succeed today?"
