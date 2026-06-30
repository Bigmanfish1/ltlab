# ltlab

An educational web application for teaching Linear Temporal Logic (LTL) model checking to Computer Science students.

Students build Kripke structures visually, write LTL formulas, and submit them for checking — returning a counterexample trace when a property does not hold.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Web framework | Django 5.1 + django-ninja (REST) |
| Database | PostgreSQL 16 (local dev) / Supabase (production) |
| LTL compute | Synchronous, in-process (SPOT C++ engine via `spottl`) |
| Frontend | Tailwind CSS (CDN), HTMX 2, Cytoscape.js 3.30 |
| Static files | WhiteNoise |
| Dev environment | VS Code / Cursor Dev Containers |
| Linting | Ruff |
| Hosting | Google Cloud Run (Docker, scale-to-zero) |

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or Docker Engine + Docker Compose (Linux/WSL2)
- [VS Code](https://code.visualstudio.com/) or [Cursor](https://cursor.com/) with the **Dev Containers** extension

---

## Quick Start (Dev Container)

This is the recommended way to develop. The dev container starts all services automatically and configures the editor.

1. **Clone the repo**

   ```bash
   git clone <repo-url>
   cd ltlab
   ```

2. **Create your `.env` file**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set a real `SECRET_KEY`. For local dev the other defaults are fine.

3. **Open in dev container**

   In VS Code / Cursor: open the command palette (`Ctrl+Shift+P`) and run:
   ```
   Dev Containers: Reopen in Container
   ```

   The first run builds the Docker images and installs all Python dependencies — this takes a few minutes. Subsequent starts are fast.

4. **Verify**

   Open `http://localhost:8000` — you should see the ltlab landing page.
   The API health check is at `http://localhost:8000/api/health`.
   Interactive API docs are at `http://localhost:8000/api/docs`.

---

## Running Without Dev Containers

If you prefer to run the stack without attaching the editor:

```bash
cp .env.example .env
docker compose up --build
```

The app will be available at `http://localhost:8000`.

---

## Environment Variables

All variables are read from `.env` (see `.env.example`). Never commit `.env`.

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key — generate a random value for any real deployment | `generate-a-real-secret-key` |
| `DEBUG` | Django debug mode | `True` |
| `DJANGO_SETTINGS_MODULE` | Settings module to load | `config.settings.development` |
| `DATABASE_URL` | Full Postgres connection URL | `postgres://ltlab:change-me@db:5432/ltlab` |
| `POSTGRES_DB` | Database name (used by Postgres container) | `ltlab` |
| `POSTGRES_USER` | Database user | `ltlab` |
| `POSTGRES_PASSWORD` | Database password | `change-me` |
| `ALLOWED_HOSTS` | Comma-separated list of allowed hosts | `localhost,127.0.0.1,0.0.0.0` |
| `RUN_MIGRATIONS` | Controls whether migrations run on container startup. Hardcoded to `true` in `docker-compose.yml` for local dev — not set in production (Cloud Run migrates via the `ltlab-migrate` Job, not on instance startup). | not set |

---

## Authentication

Sign-in is **Google OAuth via Supabase** (PKCE flow) — not Django's built-in auth.
A user clicks "Continue with Google", Supabase handles the OAuth handshake, and on
return the app creates/updates a `Profile` row (the `Users` table) and stores the
Supabase session in `sb-access-token` / `sb-refresh-token` cookies.

**How a request is authenticated (every page load):**

- The access token is a Supabase **JWT**, signed with an ES256 key. `SupabaseAuthMiddleware`
  verifies it **locally** — checks the signature against Supabase's public key (fetched
  once from the JWKS endpoint and cached), plus expiry/audience/issuer. No network call to
  Supabase on the hot path. If the token is expired, it silently refreshes using the refresh
  token.
- The user is linked to their `Profile` by **email** (the join key).

**Logout** revokes the session at Supabase (`scope=global` — signs the user out of *every*
device, server-side revoking the refresh token) **and** adds the session to a small in-process
denylist so the still-unexpired access token stops working immediately. The denylist is
per-process: on Cloud Run with autoscaling/scale-to-zero a token revoked on one instance can
still pass on another until its `exp` (≤1h). This gap is bounded and self-healing — once the
access token expires the session cannot be refreshed anywhere because `scope=global` already
revoked the refresh token. Accepted for now; instant cross-instance logout would need a shared
store (e.g. Firestore with native TTL).

---

## Project Structure

```
ltlab/
├── .github/
│   └── workflows/
│       └── ci.yml          # CI/CD pipeline (lint, test, scan, deploy)
├── .devcontainer/
│   └── devcontainer.json   # Dev container config
├── .env.example            # Template for required environment variables
├── docker-compose.yml      # Local services (web, db)
├── infra/
│   └── bootstrap.sh        # Idempotent IaC for Cloud Run deploy infra (no Terraform)
├── ruff.toml               # Ruff linting config (root)
└── backend/
    ├── Dockerfile
    ├── entrypoint.sh       # Runs migrations on startup if RUN_MIGRATIONS=true (local Docker)
    ├── start-web.sh        # Cloud Run entrypoint — gunicorn gthread (migrate/static run elsewhere)
    ├── manage.py
    ├── requirements.txt
    ├── ruff.toml
    ├── config/
    │   ├── api.py          # NinjaAPI root — mount app routers here
    │   ├── urls.py         # URL root (/, /admin/, /api/)
    │   ├── views.py
    │   ├── asgi.py
    │   ├── wsgi.py
    │   └── settings/
    │       ├── base.py     # Shared settings
    │       ├── development.py
    │       └── production.py  # Production settings for Cloud Run
    ├── apps/
    │   ├── accounts/       # Custom Supabase PKCE OAuth, Profile model, decorators
    │   │   ├── jwt_auth.py        # Local ES256 JWT verification + session denylist
    │   │   ├── middleware.py      # SupabaseAuthMiddleware + login/teacher decorators
    │   │   ├── auth_cookies.py    # sb-access-token / sb-refresh-token cookie helpers
    │   │   ├── constants.py
    │   │   ├── models.py          # Profile (maps to Users table)
    │   │   ├── views.py           # OAuth init + callback + logout
    │   │   └── management/commands/set_role.py   # Promote/demote teacher role
    │   ├── home/          # Landing page + dashboards
    │   ├── exercises/     # Guided exercises (mock data — no DB models yet)
    │   ├── kripke/        # Kripke structure persistence (models not implemented yet)
    │   └── checker/       # LTL engine (SPOT) + synchronous run_ltl_check in tasks.py
    ├── static/
    ├── templates/
    │   ├── base.html
    │   ├── header.html
    │   ├── home.html
    │   ├── accounts/      # login.html
    │   ├── dashboard/     # student_dashboard.html, teacher_dashboard.html
    │   ├── exercises/     # exercises.html, exercise_canvas.html
    │   └── sandbox/       # sandbox.html, result.html, counterexample.html
    └── tests/             # Mirrors apps/ tree; kept out of prod packages
        └── accounts/      # test_auth.py
```

---

## CI/CD Pipeline

The pipeline runs automatically via GitHub Actions and consists of four sequential jobs:

```
lint → test → vulnerability-scan → deploy
```

| Job | What it does | Triggers |
|-----|-------------|---------|
| `lint` | Runs Ruff to catch syntax and undefined variable errors | Push/PR to `main` or `develop` |
| `test` | Runs Django migrations and test suite against Postgres + Redis | Push/PR to `main` or `develop` |
| `vulnerability-scan` | Builds Docker image and runs Trivy (CRITICAL/HIGH CVEs only) | Push/PR to `main`, `workflow_dispatch` |
| `deploy` | Triggers a Render deployment | Manual only (`main` branch) |

### Running Lint Locally
 
```bash
# Check for errors
docker exec -it ltlab-web ruff check .
 
# Auto-fix where possible
docker exec -it ltlab-web ruff check . --fix
```

### Triggering a Deployment

Deployments are manual and can only be triggered from the `main` branch:

1. Go to **Actions** tab on GitHub
2. Click **CI/CD** workflow
3. Click **Run workflow**
4. Select `main` branch
5. Click **Run workflow**

The pipeline runs lint, test, and scan — if all three pass, the deploy job runs and triggers a Render deployment.

---

## Production Deployment

Live at **https://ltlab-87262955263.us-central1.run.app** — a single Google Cloud Run
service (`ltlab`, region `us-central1`, scale-to-zero) running gunicorn (gthread) via
`backend/start-web.sh`. There is no Celery worker and no Redis: the LTL check runs
synchronously in the request, and classroom bursts are absorbed by Cloud Run autoscaling
instances horizontally.

**Instance startup runs gunicorn only.** Migrations and static are *not* run per-instance
(that would race across autoscaled instances):
- **Migrations** run once per deploy as the **`ltlab-migrate` Cloud Run Job**
  (`migrate --fake-initial`), executed by the deploy pipeline before the new revision serves.
- **`collectstatic`** runs at **Docker build time** (a `RUN` layer in the Dockerfile), baked
  into the image.

**Required env vars** (set on the Cloud Run service, never committed): `DJANGO_SETTINGS_MODULE`,
`DEBUG`, `ALLOWED_HOSTS`, `SECRET_KEY`, `DATABASE_URL` (Supabase session pooler, port 5432),
`SUPABASE_URL`, `SUPABASE_ANON_KEY`. `REDIS_URL` is intentionally **not** set — cache falls back
to per-process `LocMemCache`, correct on autoscaled instances.

**To check it's working:**
- `curl https://ltlab-87262955263.us-central1.run.app/api/health` → expect
  `200 {"status": "ok", "service": "ltlab"}`

**Cold start:** the service scales to zero when idle; the next request cold-starts the instance
(a few seconds), then serves normally.

> Deploy infra (deploy SA, Workload Identity Federation, Artifact Registry repo, Cloud Run
> service, `ltlab-migrate` Job) is recreated idempotently by `infra/bootstrap.sh` — the source
> of truth, no Terraform.

---

## Common Commands

All `manage.py` commands must run **inside the `ltlab-web` container**:

```bash
# Run a management command
docker exec -it ltlab-web python manage.py <command>

# Create and apply migrations
docker exec -it ltlab-web python manage.py makemigrations
docker exec -it ltlab-web python manage.py migrate

# Create a superuser for /admin/
docker exec -it ltlab-web python manage.py createsuperuser

# Open a Django shell
docker exec -it ltlab-web python manage.py shell

# Open a bash shell inside the container
docker exec -it ltlab-web bash
```

### Setting a User's Role (teacher / student)

Every user is a `student` by default on their first Google sign-in. To promote
someone to `teacher`, flip the role on their `Profile`.

> **The user must sign in once first** so their `Profile` row exists, otherwise
> the command errors with "No profile for `<email>`".

```bash
# Local (Docker)
docker exec -it ltlab-web python manage.py set_role <email> teacher

# Demote back to student
docker exec -it ltlab-web python manage.py set_role <email> student
```

> **Note:** logging out uses `scope=global` — it revokes *every* session for that
> user, not just the current browser. Expected in production; during local
> testing it means hitting logout signs you out everywhere.

### Checking Logs

```bash
docker logs -f ltlab-web      # Django dev server
docker logs -f ltlab-db       # PostgreSQL
```

### Rebuilding After Dependency Changes

If you add packages to `requirements.txt`:

```bash
docker compose build --no-cache web
docker compose up
```

---

## API

The REST API is built with [django-ninja](https://django-ninja.dev/) and mounted at `/api/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Service health check |
| — | `/api/docs` | Swagger UI |
| — | `/api/openapi.json` | OpenAPI schema |

New routers should be created in `apps/<app>/api.py` and mounted in `config/api.py`.

---

## Architecture

```
Browser
  │  HTTP
  ▼
Google Cloud Run — service `ltlab`   ← autoscales instances on load
  └─ Gunicorn (gthread, Django + WhiteNoise)
       │  LTL check runs synchronously in-process (SPOT)
       │  DB queries
       ▼
Supabase Postgres
```

The LTL check runs synchronously inside the request — a typical check completes in a few
milliseconds (measured on small graphs), and the validation caps (≤100 states; ≤8 atomic
propositions / ≤10 temporal operators / ≤40 formula nodes) bound the work, so there is no task
queue, no Celery, and no Redis. Concurrency under classroom bursts is handled by Cloud Run
autoscaling instances horizontally.

Local development replaces Supabase Postgres with a local Docker Postgres container (see
`docker-compose.yml`). Migrations run automatically on local startup when `RUN_MIGRATIONS=true`
is hardcoded in `docker-compose.yml`; in production they run via the `ltlab-migrate` Cloud Run
Job (see Production Deployment above), not on instance startup.
