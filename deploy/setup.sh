#!/usr/bin/env bash
# One-time project bootstrap for Show List on Google Cloud.
# Enables APIs, creates the Artifact Registry repo, grants the runtime/build
# service account the roles it needs, and pushes secrets from a local .env.
# Idempotent: safe to re-run. Run this before deploy/{webhook,poller,scheduler}.sh.
#
# Prerequisites you must do yourself:
#   - gcloud auth login <your-account>
#   - Billing linked to the project:
#       gcloud billing projects link <PROJECT> --billing-account <ACCOUNT_ID>
#   - A Firestore (default) database in Native mode in your region.
#
# Usage: ./deploy/setup.sh [project-id] [region]
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
REPO="show-list"
ENV_FILE=".env"

echo "==> Enabling APIs ..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  --project "$PROJECT"

echo "==> Ensuring Artifact Registry repo '$REPO' ..."
gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" \
       --repository-format=docker --location "$REGION" --project "$PROJECT"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "==> Granting the runtime/build service account ($COMPUTE_SA) its roles ..."
# Reads mounted secrets at runtime:
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${COMPUTE_SA}" \
  --role roles/secretmanager.secretAccessor --condition=None >/dev/null
# Used as the Cloud Build service account for --source/builds submit:
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${COMPUTE_SA}" \
  --role roles/cloudbuild.builds.builder --condition=None >/dev/null

echo "==> Pushing secrets from $ENV_FILE ..."
for KEY in TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_PHONE_NUMBER TWILIO_WHATSAPP_NUMBER \
           SEATGEEK_CLIENT_ID SEATGEEK_CLIENT_SECRET GEMINI_API_KEY GCP_PROJECT_ID; do
  VALUE="$(grep "^${KEY}=" "$ENV_FILE" | cut -d= -f2-)"
  if [ -z "$VALUE" ]; then echo "   !! $KEY missing/empty in $ENV_FILE — skipping"; continue; fi
  if gcloud secrets describe "$KEY" --project "$PROJECT" >/dev/null 2>&1; then
    printf "%s" "$VALUE" | gcloud secrets versions add "$KEY" --data-file=- --project "$PROJECT" >/dev/null
    echo "   updated $KEY"
  else
    printf "%s" "$VALUE" | gcloud secrets create "$KEY" \
      --replication-policy=automatic --data-file=- --project "$PROJECT" >/dev/null
    echo "   created $KEY"
  fi
done

echo "Setup complete. Next: ./deploy/webhook.sh && ./deploy/poller.sh && ./deploy/scheduler.sh"
