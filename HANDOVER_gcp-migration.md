# HANDOVER — GCP Cloud Run migration

> Untracked scratch doc for the next agent. Do NOT commit (project rule: never commit HANDOVER_*.md).

## What this work is
Migrated LTLab off Render onto **Google Cloud Run**, removed Celery/Redis (LTL
check now runs **synchronously in-request**), rewired CI/CD to deploy to Cloud
Run via **Workload Identity Federation**, then fixed two rounds of code review
(engine error-handling, deploy/runtime hardening) and added infra-as-code.

## Branch / git state
- Branch: **`feature/gcp-cloud-run-migration`** (off `develop`), pushed to origin.
- **Not yet PR'd** to `develop`. Opening the PR is the next milestone.
- Repo: `Bigmanfish1/ltlab`. `gh` CLI authed as `Morgan-Bentley`.
- CLAUDE.md is **gitignored** (local only). HANDOVER_*.md untracked. No Co-Authored-By trailers; no Claude-related files committed (teammates don't use Claude).

## Live state (verified)
- Service **`ltlab`** live at `https://ltlab-87262955263.us-central1.run.app` — login + sandbox Verify work in prod. Currently serving revision **`ltlab-00004-csw`**, built by the OLD `--source` deploy path (image in `cloud-run-source-deploy` repo).
- 141 tests pass, ruff clean.

## ⚠️ CRITICAL pending step — run bootstrap before the new CI deploy
The CI deploy job was rewritten to **build → migrate (Job) → deploy** using the
`ltlab` Artifact Registry repo and the `ltlab-migrate` Cloud Run Job. **Neither
exists in GCP yet** — they're created by `infra/bootstrap.sh`. **If anyone
triggers a deploy now, it fails** at the migrate step (`gcloud run jobs update
ltlab-migrate` → not found).

To make the new pipeline work:
1. Export secrets: `SECRET_KEY` (reuse the EXISTING one from the live service to
   avoid logging everyone out — read it: `gcloud run services describe ltlab
   --region us-central1 --format='value(spec.template.spec.containers[0].env)'`),
   `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`.
2. `./infra/bootstrap.sh` (creates repo + Job, idempotent; preserves existing infra).
3. Test the new pipeline: temporarily add `feature/gcp-cloud-run-migration` to the
   deploy job's `if` gate (it's a known pattern in this branch's history), push,
   run the GitHub "CI/CD" workflow on the branch, verify the new revision in GCP,
   then REVERT the temp gate.
4. Then PR → `develop`.

## GCP infra reference (facts)
- Project `project-10a4cc96-5dd3-4010-8a3` (number `87262955263`), region **`us-central1`** (always-free tier — do NOT move to EU or it leaves free tier; trial credit $300 until ~2026-09-27).
- gcloud at `/root/google-cloud-sdk/bin/gcloud`, authed as **`ltlab700@gmail.com`** (Owner) — a NEW gmail, separate from OAuth.
- **Google OAuth client is in a SEPARATE project `ltlab-499310` (old gmail), unchanged by hosting.** Supabase brokers Google login. Don't touch it. Supabase → Auth → URL Configuration must list the Cloud Run callback `…/accounts/callback/`.
- WIF: pool `github-pool`, provider `github-provider`; SA `gh-deploy@…`; SA binding repo-scoped to `Bigmanfish1/ltlab` (verified). CI auth is keyless.
- Runtime env vars (SECRET_KEY, DATABASE_URL, SUPABASE_*, etc.) live ONLY on the Cloud Run service + `ltlab-migrate` Job — not in the repo. `.env.example` documents the names.
- `DATABASE_URL` must be the Supabase **session pooler** (host `…pooler.supabase.com`, **port 5432**, IPv4). Direct DB is IPv6-only (unreachable from Cloud Run); transaction pooler (6543, `?pgbouncer=true`) breaks Django/psycopg prepared statements.

## Non-obvious technical facts (measured/verified this work)
- **SPOT holds the GIL** during its C++ calls (measured 1.13× across 2 threads) → threads give NO CPU parallelism; per-instance parallelism = processes, bursts = Cloud Run autoscaling instances. gthread is for request concurrency only.
- A check is **~a few ms** on small graphs (measured ~1-3ms). Caps: ≤100 states (view `MAX_NODES`), ≤8 APs / ≤10 temporal ops / ≤40 formula nodes (`engine.validate_request`). "15 states" was a wrong comment, since corrected.
- `spottl` wheel is **manylinux x86_64 only** → Cloud Run amd64 is fine, but ARM-Mac local builds need `--platform linux/amd64`.
- `--timeout 30` in gunicorn is the runaway-check backstop: a hang in SPOT's C++ silences the gthread heartbeat → arbiter SIGKILLs the worker. A Python signal/alarm can't interrupt a C call. (Verified via gunicorn docs/source.)
- No `--max-requests`: no measured leak (~5MB/500), Cloud Run OOM-recycles, so recycling the lone worker only re-pays the ~1s import.
- Logout denylist is **in-process** (decision "B2") — revocation doesn't propagate across autoscaled instances; bounded ≤1h (access-token life) and self-healing (Supabase `scope=global` revokes the refresh token at logout). Firestore-backed shared denylist is the upgrade if instant cross-instance logout is ever needed.

## Known pre-existing issue (NOT introduced here)
Engine labels response-property violations (`G(req → F grant)`) as **safety**
instead of **liveness** in the educational annotation only (never affects the
satisfied/violated verdict). Root cause in `engine._classify` (`G ψ` branch:
`live_body` misses an implication whose consequent is the eventuality). It is
**test-locked** (`test_response_property_marks_p_state_violating`). engine.py was
unchanged by this migration. Fixing it means updating that locked test.

## Files that matter
- `backend/Dockerfile` — prod settings default + build-time collectstatic
- `backend/start-web.sh` — gunicorn only
- `.github/workflows/ci.yml` — build→migrate→deploy (WIF)
- `infra/bootstrap.sh` — recreates all infra
- `apps/checker/views.py` `verify_ltl` — sync engine call + catch-all error banner
- `apps/checker/tasks.py` `run_ltl_check` — plain sync function (no Celery)
