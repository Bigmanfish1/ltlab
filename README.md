# ltlab

An educational web application for teaching Linear Temporal Logic (LTL) model checking to Computer Science students.

Students build Kripke structures visually, write LTL formulas, and submit them for checking — returning a counterexample trace when a property does not hold.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Web framework | Django 5.1 + django-ninja (REST) |
| Database | PostgreSQL 16 (local dev) / Supabase (production) |
| Task queue | Celery 5 + Redis 7 |
| Frontend | Tailwind CSS (CDN), HTMX 2, Cytoscape.js 3.30 |
| Static files | WhiteNoise |
| Dev environment | VS Code / Cursor Dev Containers |
| Linting | Ruff |
| Hosting | Render (backend) |

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
| `REDIS_URL` | Redis connection URL (broker + result backend) | `redis://redis:6379/0` |
| `ALLOWED_HOSTS` | Comma-separated list of allowed hosts | `localhost,127.0.0.1,0.0.0.0` |
| `RUN_MIGRATIONS` | Controls whether migrations run on container startup. Hardcoded to `true` in `docker-compose.yml` for local dev — do not set this on Render. | not set |

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
├── docker-compose.yml      # All four services (web, worker, db, redis)
├── render.yaml             # Render deployment config
├── ruff.toml               # Ruff linting config
└── backend/
    ├── Dockerfile
    ├── entrypoint.sh       # Runs migrations on startup if RUN_MIGRATIONS=true
    ├── manage.py
    ├── requirements.txt
    ├── config/
    │   ├── api.py          # NinjaAPI root — mount app routers here
    │   ├── celery.py       # Celery app definition
    │   ├── urls.py         # URL root (/, /admin/, /api/)
    │   └── settings/
    │       ├── base.py     # Shared settings
    │       ├── development.py
    │       └── production.py  # Production settings for Render
    ├── apps/
    │   ├── accounts/       # User auth & student profiles (stub)
    │   ├── exercises/      # Guided exercises (stub)
    │   ├── kripke/         # Kripke structure persistence (stub)
    │   └── checker/        # LTL checking engine + Celery task (stub)
    └── templates/
        ├── base.html
        └── home.html
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

Live at **https://ltlab.onrender.com** — a single Render web service (`ltlab`) running gunicorn *and* an in-process Celery worker together via `backend/start-web.sh`. (Render's free tier has no free Background Worker tier, so Celery runs as a backgrounded process inside the same container instead of as a separate service — see `render.yaml` comments / git history if you need the full reasoning.)

**To deploy:** push to `main`, then manually trigger the workflow — Actions tab → **CI/CD** → **Run workflow** → `main`. If lint/test/scan pass, the `deploy` job POSTs to the `RENDER_DEPLOY_HOOK` secret and Render rebuilds + redeploys.

**To check it's working:**
- `curl https://ltlab.onrender.com/api/health` → expect `200 {"status": "ok", "service": "ltlab"}`
- Render dashboard → `ltlab` → **Logs** → look for `celery@<hostname> ready.` with **no traceback after it** (confirms the in-process worker connected to Redis)

**Resuming after a pause / spin-down:**
- Free Render web services spin down after ~15 min idle — this also stops the backgrounded Celery worker, since it's the same container. Just hit the URL again; it cold-starts in ~30–60s and both come back up together.
- Manually suspended via Render dashboard (`ltlab` → Settings → Suspend Web Service)? Resume from the same place.

**⚠️ Upstash Redis auto-archive risk:** the production `REDIS_URL` points at a free Upstash Redis instance. Upstash auto-archives free databases after a stretch of real inactivity (no `SET`/`GET`/etc — pings don't count; their docs/ToS disagree on whether it's ~7 or ~14 days). If that happens:
- Data is backed up, not lost — but restoring generates a **new `REDIS_URL` + token**, which then has to be re-pasted into Render's env vars (raw URL, no quotes, with `?ssl_cert_reqs=CERT_REQUIRED` appended)
- Whoever holds the Upstash account login handles the restore
- Cheap mitigation: running a real LTL exercise through the app every week or so during quiet stretches counts as activity and keeps the database alive

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

In **production**, run the same command without the `docker exec` prefix from the
Render service **Shell** tab (it runs against the Supabase database):

```bash
python manage.py set_role <email> teacher
```

> **Note:** logging out uses `scope=global` — it revokes *every* session for that
> user, not just the current browser. Expected in production; during local
> testing it means hitting logout signs you out everywhere.

### Checking Logs

```bash
docker logs -f ltlab-web      # Django dev server
docker logs -f ltlab-worker   # Celery worker
docker logs -f ltlab-db       # PostgreSQL
docker logs -f ltlab-redis    # Redis
```

### Rebuilding After Dependency Changes

If you add packages to `requirements.txt`:

```bash
docker compose build --no-cache web worker
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
Render — single web service `ltlab`
  ├─ Gunicorn   (Django + WhiteNoise)
  └─ Celery worker (backgrounded in same container, via start-web.sh)
       │                                  │
       │ DB queries                       │ task dispatch / results
       ▼                                  ▼
Supabase Postgres                  Upstash Redis  (rediss://, broker + result backend)
```

Local development replaces Supabase Postgres with a local Docker Postgres container, and runs Celery as its own `ltlab-worker` container (see `docker-compose.yml`) — production collapses both into one process for free-tier hosting reasons (see Production Deployment section above).
Migrations run automatically on local startup when `RUN_MIGRATIONS=true` is hardcoded in `docker-compose.yml`.
