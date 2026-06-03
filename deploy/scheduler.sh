#!/usr/bin/env bash
# Create the scheduler service account, grant it invoker on the poller job, and
# create a Cloud Scheduler job to trigger the poller daily at noon UTC.
# Idempotent: safe to re-run. Run deploy/poller.sh first (the job must exist).
# Usage: ./deploy/scheduler.sh [project-id] [region]
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
JOB_NAME="show-list-poller"
SCHEDULE="0 12 * * *"   # daily at noon UTC

JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB_NAME}:run"
SA="show-list-scheduler@${PROJECT}.iam.gserviceaccount.com"

echo "Ensuring scheduler service account exists ..."
gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud iam service-accounts create show-list-scheduler \
       --project "$PROJECT" --display-name "Show List scheduler"

echo "Granting roles/run.invoker on the poller job ..."
gcloud run jobs add-iam-policy-binding "$JOB_NAME" \
  --project "$PROJECT" --region "$REGION" \
  --member "serviceAccount:${SA}" --role roles/run.invoker

echo "Creating/updating the Cloud Scheduler job ..."
if gcloud scheduler jobs describe "${JOB_NAME}-daily" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  ACTION=update
else
  ACTION=create
fi
gcloud scheduler jobs "$ACTION" http "${JOB_NAME}-daily" \
  --location "$REGION" \
  --project "$PROJECT" \
  --schedule "$SCHEDULE" \
  --time-zone "UTC" \
  --uri "$JOB_URI" \
  --http-method POST \
  --oauth-service-account-email "$SA" \
  --message-body "{}" \
  --headers "Content-Type=application/json"

echo "Scheduler job ${JOB_NAME}-daily ${ACTION}d ($SCHEDULE UTC)."
