#!/usr/bin/env bash
#
# Recreate all GCP infrastructure for LTLab's Cloud Run deployment. Idempotent —
# safe to re-run. This is the executable record of the deploy infra (no Terraform
# state to manage); run it once to bootstrap a fresh project, or to repair drift.
#
# Prerequisites:
#   - gcloud authenticated as a project Owner/Editor (`gcloud auth login`)
#   - export the runtime secrets first (used to seed Secret Manager on a fresh
#     project; on re-run existing secret versions are kept, not overwritten):
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
# Numeric GitHub IDs for the WIF condition (immutable, unlike owner/repo names).
# Get them: gh api /repos/OWNER/REPO --jq '{id, owner_id: .owner.id}'
GH_OWNER_ID="${GH_OWNER_ID:-49813971}"
GH_REPO_ID="${GH_REPO_ID:-1246047064}"
POOL="github-pool"
PROVIDER="github-provider"
DEPLOY_SA="gh-deploy@${PROJECT_ID}.iam.gserviceaccount.com"
RUN_SA="ltlab-run@${PROJECT_ID}.iam.gserviceaccount.com"  # least-privilege runtime identity
CLOUDBUILD_BUCKET="${PROJECT_ID}_cloudbuild"              # gcloud builds submit staging bucket

# Required to seed Secret Manager / non-secret env (fail early if missing).
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
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com

echo "==> Artifact Registry repo: $REPO"
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" \
       --repository-format=docker --location="$REGION" \
       --description="LTLab container images"

echo "==> Artifact Registry cleanup policy (0.5GB free tier — prune aggressively)"
CLEANUP_FILE="$(mktemp)"
cat > "$CLEANUP_FILE" <<'JSON'
[
  {"name": "delete-untagged-30d", "action": {"type": "Delete"},
   "condition": {"tagState": "untagged", "olderThan": "2592000s"}},
  {"name": "keep-recent-3", "action": {"type": "Keep"},
   "mostRecentVersions": {"keepCount": 3}}
]
JSON
gcloud artifacts repositories set-cleanup-policies "$REPO" --location="$REGION" \
  --policy="$CLEANUP_FILE" --quiet
rm -f "$CLEANUP_FILE"

echo "==> Cloud Build staging bucket (needed to scope the deploy SA to it)"
gcloud storage buckets describe "gs://${CLOUDBUILD_BUCKET}" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${CLOUDBUILD_BUCKET}" --location="$REGION"

echo "==> Deploy service account + roles (least privilege — no project-wide storage.admin)"
gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create gh-deploy \
       --display-name="GitHub Actions Cloud Run deployer"
for ROLE in roles/run.admin roles/cloudbuild.builds.editor \
            roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" --role="$ROLE" --condition=None >/dev/null
done
# Source upload scoped to the staging bucket, not project-wide storage.admin.
gcloud storage buckets add-iam-policy-binding "gs://${CLOUDBUILD_BUCKET}" \
  --member="serviceAccount:${DEPLOY_SA}" --role="roles/storage.objectAdmin" >/dev/null

echo "==> Cloud Build (Compute SA) roles"
for ROLE in roles/cloudbuild.builds.builder roles/storage.objectViewer \
            roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${COMPUTE_SA}" --role="$ROLE" --condition=None >/dev/null
done

echo "==> Runtime service account: $RUN_SA (service + migrate Job identity)"
gcloud iam service-accounts describe "$RUN_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create ltlab-run \
       --display-name="LTLab Cloud Run runtime identity"

echo "==> Workload Identity Federation (pool/provider/binding)"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$POOL" \
       --location=global --display-name="GitHub Actions Pool"
# Provider condition uses immutable numeric IDs (not name-based repository_owner).
PROVIDER_CONDITION="assertion.repository_owner_id == '${GH_OWNER_ID}' && assertion.repository_id == '${GH_REPO_ID}'"
if gcloud iam workload-identity-pools providers describe "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" \
    --attribute-condition="$PROVIDER_CONDITION"
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" --display-name="GitHub provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="$PROVIDER_CONDITION" \
    --issuer-uri="https://token.actions.githubusercontent.com"
fi
# Binding is repo-scoped: only GH_REPO (not the whole owner) may impersonate.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GH_REPO}" >/dev/null

echo "==> Secret Manager: SECRET_KEY + DATABASE_URL (created once; rotate with 'versions add')"
ensure_secret() {  # $1 name, $2 value: create first version if absent, grant RUN_SA read
  gcloud secrets describe "$1" >/dev/null 2>&1 \
    || printf '%s' "$2" | gcloud secrets create "$1" --replication-policy=automatic --data-file=-
  gcloud secrets add-iam-policy-binding "$1" \
    --member="serviceAccount:${RUN_SA}" --role="roles/secretmanager.secretAccessor" >/dev/null
}
ensure_secret ltlab-secret-key   "$SECRET_KEY"
ensure_secret ltlab-database-url "$DATABASE_URL"
# Pin to a version, not :latest — env-mounted secrets resolve at instance start.
latest_ver() { gcloud secrets versions list "$1" --filter="state=ENABLED" --sort-by="~name" --limit=1 --format="value(name)"; }
SK_VER="$(latest_ver ltlab-secret-key)"
DU_VER="$(latest_ver ltlab-database-url)"
SECRETS_FLAG="SECRET_KEY=ltlab-secret-key:${SK_VER},DATABASE_URL=ltlab-database-url:${DU_VER}"

# Non-secret runtime env for service + Job. YAML file (not --set-env-vars, which
# splits on ',' and would corrupt comma-bearing values). SECRET_KEY/DATABASE_URL
# come from Secret Manager via SECRETS_FLAG, not here.
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
emit_env() { printf "%s: '%s'\n" "$1" "${2//\'/\'\'}" >> "$ENV_FILE"; }
emit_env DJANGO_SETTINGS_MODULE config.settings.production
emit_env DEBUG False
emit_env ALLOWED_HOSTS .run.app
emit_env SUPABASE_URL "$SUPABASE_URL"
emit_env SUPABASE_ANON_KEY "$SUPABASE_ANON_KEY"

# Placeholder image so the service/Job can be created; CI replaces it with the
# real build on the next deploy.
PLACEHOLDER="us-docker.pkg.dev/cloudrun/container/hello"

echo "==> Migrate Job: $MIGRATE_JOB"
if gcloud run jobs describe "$MIGRATE_JOB" --region="$REGION" >/dev/null 2>&1; then
  gcloud run jobs update "$MIGRATE_JOB" --region="$REGION" \
    --service-account="$RUN_SA" --env-vars-file="$ENV_FILE" --set-secrets="$SECRETS_FLAG"
else
  gcloud run jobs create "$MIGRATE_JOB" --region="$REGION" --image="$PLACEHOLDER" \
    --service-account="$RUN_SA" --env-vars-file="$ENV_FILE" --set-secrets="$SECRETS_FLAG" \
    --command=python --args=manage.py,migrate,--fake-initial,--noinput
fi

echo "==> Cloud Run service: $SERVICE"
if gcloud run services describe "$SERVICE" --region="$REGION" >/dev/null 2>&1; then
  gcloud run services update "$SERVICE" --region="$REGION" \
    --service-account="$RUN_SA" --env-vars-file="$ENV_FILE" --set-secrets="$SECRETS_FLAG"
else
  gcloud run deploy "$SERVICE" --region="$REGION" --image="$PLACEHOLDER" \
    --service-account="$RUN_SA" \
    --min-instances=0 --max-instances=20 --concurrency=8 --cpu=1 --memory=512Mi --timeout=300 \
    --cpu-boost --allow-unauthenticated --env-vars-file="$ENV_FILE" --set-secrets="$SECRETS_FLAG"
fi

echo "==> Done. Trigger the GitHub 'CI/CD' workflow to build + migrate + deploy the real image."
