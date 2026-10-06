#!/usr/bin/env bash
# Requires an authenticated gcloud CLI and an active billing account.
set -euo pipefail

PROJECT_ID="${1:?Usage: bash deploy/cloud-run.sh PROJECT_ID [REGION]}"
REGION="${2:-europe-west1}"
SERVICE=warera-mcp
ACCOUNT=warera-mcp-runtime
RUNTIME_IDENTITY="${ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"
cd "$(dirname "$0")/.."

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com iam.googleapis.com \
  --project="$PROJECT_ID"

# New projects do not automatically grant the build identity the builder role.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --quiet

if ! gcloud iam service-accounts describe "$RUNTIME_IDENTITY" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$ACCOUNT" \
    --display-name="WarEra MCP runtime" --project="$PROJECT_ID"
fi

# First deploy behind IAM; configure the exact assigned hostname before opening access.
gcloud run deploy "$SERVICE" --source=. --project="$PROJECT_ID" --region="$REGION" \
  --service-account="$RUNTIME_IDENTITY" --no-allow-unauthenticated \
  --port=8080 --cpu=1 --memory=512Mi --concurrency=8 \
  --min=0 --max=1 --min-instances=0 --max-instances=1 --timeout=60s \
  --set-env-vars="WARERA_MCP_REQUIRE_CLIENT_AUTH=false,WARERA_MCP_STATELESS_HTTP=true,WARERA_MCP_TRUSTED_HOSTS=localhost,WARERA_MCP_JSON_RESPONSE=true,WARERA_MCP_HEALTH_PATH=/health" \
  --startup-probe="httpGet.path=/health,httpGet.port=8080,periodSeconds=5,timeoutSeconds=2,failureThreshold=24" \
  --quiet

SERVICE_URL="$(gcloud run services describe "$SERVICE" --project="$PROJECT_ID" \
  --region="$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --project="$PROJECT_ID" --region="$REGION" \
  --update-env-vars="WARERA_MCP_TRUSTED_HOSTS=${SERVICE_URL#https://}" --quiet
gcloud run services add-iam-policy-binding "$SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --member=allUsers --role=roles/run.invoker --quiet

printf '\nMCP endpoint: %s/mcp\nHealth check: %s/health\n' "$SERVICE_URL" "$SERVICE_URL"
