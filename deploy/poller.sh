#!/usr/bin/env bash
# Deploy the poller as a Cloud Run Job.
# Usage: ./deploy/poller.sh [project-id] [region]
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
JOB="show-list-poller"

echo "Deploying Cloud Run Job $JOB to $PROJECT / $REGION ..."

gcloud run jobs deploy "$JOB" \
  --source . \
  --dockerfile poller/Dockerfile \
  --project "$PROJECT" \
  --region "$REGION" \
  --set-secrets \
    "TWILIO_ACCOUNT_SID=TWILIO_ACCOUNT_SID:latest,\
TWILIO_AUTH_TOKEN=TWILIO_AUTH_TOKEN:latest,\
TWILIO_PHONE_NUMBER=TWILIO_PHONE_NUMBER:latest,\
TWILIO_WHATSAPP_NUMBER=TWILIO_WHATSAPP_NUMBER:latest,\
SEATGEEK_CLIENT_ID=SEATGEEK_CLIENT_ID:latest,\
SEATGEEK_CLIENT_SECRET=SEATGEEK_CLIENT_SECRET:latest,\
GCP_PROJECT_ID=GCP_PROJECT_ID:latest"

echo "Done. Job $JOB deployed."
echo "Run manually with: gcloud run jobs execute $JOB --region $REGION"
