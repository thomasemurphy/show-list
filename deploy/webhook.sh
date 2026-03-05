#!/usr/bin/env bash
# Deploy the webhook Flask service to Cloud Run.
# Usage: ./deploy/webhook.sh [project-id] [region]
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
SERVICE="show-list-webhook"

echo "Deploying $SERVICE to $PROJECT / $REGION ..."

gcloud run deploy "$SERVICE" \
  --source . \
  --dockerfile webhook/Dockerfile \
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
GCP_PROJECT_ID=GCP_PROJECT_ID:latest"

echo "Done. Set the Cloud Run service URL as your Twilio webhook:"
gcloud run services describe "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --format "value(status.url)"
