#!/usr/bin/env bash
# Build and deploy the poller as a Cloud Run Job.
# Usage: ./deploy/poller.sh [project-id] [region]
#
# Run deploy/setup.sh once first (APIs, Artifact Registry repo, IAM, secrets).
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
JOB="show-list-poller"
REPO="show-list"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/poller:latest"

# Build with Cloud Build (see deploy/webhook.sh for why --source isn't used).
echo "Building $IMAGE ..."
CONFIG="$(mktemp)"
cat > "$CONFIG" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -f, poller/Dockerfile, -t, '${IMAGE}', .]
images: ['${IMAGE}']
EOF
gcloud builds submit . --config "$CONFIG" --project "$PROJECT"
rm -f "$CONFIG"

echo "Deploying Cloud Run Job $JOB to $PROJECT / $REGION ..."
# GEMINI_API_KEY is included even though the poller doesn't call Gemini: the
# shared config module (shared/config.py) requires it at import time for every
# process, so the job won't start without it.
gcloud run jobs deploy "$JOB" \
  --image "$IMAGE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --set-secrets \
    "TWILIO_ACCOUNT_SID=TWILIO_ACCOUNT_SID:latest,\
TWILIO_AUTH_TOKEN=TWILIO_AUTH_TOKEN:latest,\
TWILIO_PHONE_NUMBER=TWILIO_PHONE_NUMBER:latest,\
TWILIO_WHATSAPP_NUMBER=TWILIO_WHATSAPP_NUMBER:latest,\
SEATGEEK_CLIENT_ID=SEATGEEK_CLIENT_ID:latest,\
SEATGEEK_CLIENT_SECRET=SEATGEEK_CLIENT_SECRET:latest,\
GEMINI_API_KEY=GEMINI_API_KEY:latest,\
GCP_PROJECT_ID=GCP_PROJECT_ID:latest"

echo "Done. Job $JOB deployed."
echo "Run manually with: gcloud run jobs execute $JOB --region $REGION --project $PROJECT"
