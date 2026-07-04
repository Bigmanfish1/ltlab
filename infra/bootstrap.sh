#!/usr/bin/env bash
#
# Recreate all GCP infrastructure for LTLab's Cloud Run deployment. Idempotent —
# safe to re-run. This is the executable record of the deploy infra (no Terraform
# state to manage); run it once to bootstrap a fresh project, or to repair drift.
#
# Prerequisites:
#   - gcloud authenticated as a project Owner/Editor (`gcloud auth login`)
#   - export the runtime secrets first:
#       export SECRET_KEY=...           # python -c "import secrets; print(secrets.token_urlsafe(50))"
#       export DATABASE_URL=...         # Supabase transaction pooler URI, port 6543
#       export SUPABASE_URL=...
#       export SUPABASE_ANON_KEY=...
#
# Usage:  PROJECT_ID=... REGION=... ./infra/bootstrap.sh
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-project-10a4cc96-5dd3-4010-8a3}"
REGION="${REGION:-us-central1}"
SERVICE="ltlab"
MIGRATE_JOB="ltlab-migrate"
REPO="ltlab"                 # Artifact Registry repo
GH_REPO="Bigmanfish1/ltlab"  # repo allowed to deploy via WIF
POOL="github-pool"
PROVIDER="github-provider"
DEPLOY_SA="gh-deploy@${PROJECT_ID}.iam.gserviceaccount.com"

# Required runtime secrets (fail early if missing).
: "${SECRET_KEY:?export SECRET_KEY}"
: "${DATABASE_URL:?export DATABASE_URL}"
: "${SUPABASE_URL:?export SUPABASE_URL}"
: "${SUPABASE_ANON_KEY:?export SUPABASE_ANON_KEY}"

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com

echo "==> Artifact Registry repo: $REPO"
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" \
       --repository-format=docker --location="$REGION" \
       --description="LTLab container images"

echo "==> Deploy service account + roles"
gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create gh-deploy \
       --display-name="GitHub Actions Cloud Run deployer"
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/storage.admin \
            roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" --role="$ROLE" --condition=None >/dev/null
done

echo "==> Cloud Build (Compute SA) roles"
for ROLE in roles/cloudbuild.builds.builder roles/storage.objectViewer \
            roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${COMPUTE_SA}" --role="$ROLE" --condition=None >/dev/null
done

echo "==> Workload Identity Federation (pool/provider/binding)"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$POOL" \
       --location=global --display-name="GitHub Actions Pool"
gcloud iam workload-identity-pools providers describe "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
       --location=global --workload-identity-pool="$POOL" --display-name="GitHub provider" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
       --attribute-condition="assertion.repository_owner == 'Bigmanfish1'" \
       --issuer-uri="https://token.actions.githubusercontent.com"
# Binding is repo-scoped: only GH_REPO (not the whole owner) may impersonate.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GH_REPO}" >/dev/null

# Runtime env, applied identically to the service and the migrate Job. Written
# as a YAML file rather than --set-env-vars: that flag splits values on ',', so a
# secret containing a comma (e.g. a rotated DB password) would silently corrupt
# the env. Single-quoted YAML escapes every special char ("'" -> "''").
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
emit_env() { printf "%s: '%s'\n" "$1" "${2//\'/\'\'}" >> "$ENV_FILE"; }
emit_env DJANGO_SETTINGS_MODULE config.settings.production
emit_env DEBUG False
emit_env ALLOWED_HOSTS .run.app
emit_env SECRET_KEY "$SECRET_KEY"
emit_env DATABASE_URL "$DATABASE_URL"
emit_env SUPABASE_URL "$SUPABASE_URL"
emit_env SUPABASE_ANON_KEY "$SUPABASE_ANON_KEY"

# Placeholder image so the service/Job can be created; CI replaces it with the
# real build on the next deploy.
PLACEHOLDER="us-docker.pkg.dev/cloudrun/container/hello"

echo "==> Migrate Job: $MIGRATE_JOB"
if gcloud run jobs describe "$MIGRATE_JOB" --region="$REGION" >/dev/null 2>&1; then
  gcloud run jobs update "$MIGRATE_JOB" --region="$REGION" --env-vars-file="$ENV_FILE"
else
  gcloud run jobs create "$MIGRATE_JOB" --region="$REGION" --image="$PLACEHOLDER" \
    --env-vars-file="$ENV_FILE" \
    --command=python --args=manage.py,migrate,--fake-initial,--noinput
fi

echo "==> Cloud Run service: $SERVICE"
if gcloud run services describe "$SERVICE" --region="$REGION" >/dev/null 2>&1; then
  gcloud run services update "$SERVICE" --region="$REGION" --env-vars-file="$ENV_FILE"
else
  gcloud run deploy "$SERVICE" --region="$REGION" --image="$PLACEHOLDER" \
    --min-instances=0 --max-instances=20 --concurrency=8 --cpu=1 --memory=512Mi --timeout=300 \
    --cpu-boost --allow-unauthenticated --env-vars-file="$ENV_FILE"
fi

echo "==> Done. Trigger the GitHub 'CI/CD' workflow to build + migrate + deploy the real image."
