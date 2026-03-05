#!/usr/bin/env bash
# Create a Cloud Scheduler job to trigger the poller daily at noon UTC.
# Usage: ./deploy/scheduler.sh [project-id] [region]
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
JOB_NAME="show-list-poller"
SCHEDULE="0 12 * * *"   # daily at noon UTC

# Cloud Run Job URI
JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB_NAME}:run"

# Service account for scheduler (must have roles/run.invoker on the job)
SA="show-list-scheduler@${PROJECT}.iam.gserviceaccount.com"

echo "Creating Cloud Scheduler job for $JOB_NAME ..."

gcloud scheduler jobs create http "$JOB_NAME-daily" \
  --location "$REGION" \
  --project "$PROJECT" \
  --schedule "$SCHEDULE" \
  --time-zone "UTC" \
  --uri "$JOB_URI" \
  --http-method POST \
  --oauth-service-account-email "$SA" \
  --message-body "{}" \
  --headers "Content-Type=application/json"

echo "Scheduler job created: $JOB_NAME-daily ($SCHEDULE UTC)"
echo ""
echo "Make sure the service account $SA has roles/run.invoker."
echo "If it doesn't exist yet:"
echo "  gcloud iam service-accounts create show-list-scheduler --project $PROJECT"
echo "  gcloud run jobs add-iam-policy-binding $JOB_NAME \\"
echo "    --region $REGION --member serviceAccount:$SA --role roles/run.invoker"
