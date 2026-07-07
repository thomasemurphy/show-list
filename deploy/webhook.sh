#!/usr/bin/env bash
# Build and deploy the webhook Flask service to Cloud Run.
# Usage: ./deploy/webhook.sh [project-id] [region]
#
# Run deploy/setup.sh once first (APIs, Artifact Registry repo, IAM, secrets).
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
SERVICE="show-list-webhook"
REPO="show-list"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/webhook:latest"

# Build with Cloud Build. We can't use `gcloud run deploy --source` here: the
# Dockerfile lives at webhook/Dockerfile and builds from the repo root (it COPYs
# shared/ and webhook/), and `--dockerfile` isn't supported across gcloud
# versions. So build explicitly via a generated Cloud Build config.
echo "Building $IMAGE ..."
CONFIG="$(mktemp)"
cat > "$CONFIG" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -f, webhook/Dockerfile, -t, '${IMAGE}', .]
images: ['${IMAGE}']
EOF
gcloud builds submit . --config "$CONFIG" --project "$PROJECT"
rm -f "$CONFIG"

echo "Deploying $SERVICE to $PROJECT / $REGION ..."
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --set-secrets \
    "TWILIO_ACCOUNT_SID=TWILIO_ACCOUNT_SID:latest,\
TWILIO_AUTH_TOKEN=TWILIO_AUTH_TOKEN:latest,\
TWILIO_PHONE_NUMBER=TWILIO_PHONE_NUMBER:latest,\
TWILIO_WHATSAPP_NUMBER=TWILIO_WHATSAPP_NUMBER:latest,\
SEATGEEK_CLIENT_ID=SEATGEEK_CLIENT_ID:latest,\
SEATGEEK_CLIENT_SECRET=SEATGEEK_CLIENT_SECRET:latest,\
GEMINI_API_KEY=GEMINI_API_KEY:latest,\
GCP_PROJECT_ID=GCP_PROJECT_ID:latest,\
INTERNAL_API_SHARED_SECRET=INTERNAL_API_SHARED_SECRET:latest"

echo "Done. Set the Cloud Run service URL as your Twilio webhook (append /webhook):"
gcloud run services describe "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --format "value(status.url)"
