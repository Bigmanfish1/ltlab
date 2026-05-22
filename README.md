# ltlab

An educational web application for teaching Linear Temporal Logic (LTL) model checking to Computer Science students.

Students build Kripke structures visually, write LTL formulas, and submit them to a background worker that checks whether the property holds — returning a counterexample trace when it does not.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Web framework | Django 5.1 + django-ninja (REST) |
| Database | PostgreSQL 16 |
| Task queue | Celery 5 + Redis 7 |
| Frontend | Tailwind CSS (CDN), HTMX 2, Cytoscape.js 3.30 |
| Static files | WhiteNoise |
| Dev environment | VS Code / Cursor Dev Containers |

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

---

## Project Structure

```
ltlab/
├── .cursor/rules/          # Cursor AI agent rules (project conventions)
├── .devcontainer/
│   └── devcontainer.json   # Dev container config
├── .env.example            # Template for required environment variables
├── docker-compose.yml      # All four services (web, worker, db, redis)
└── backend/
    ├── Dockerfile
    ├── entrypoint.sh       # Runs migrations then execs CMD
    ├── manage.py
    ├── requirements.txt
    ├── config/
    │   ├── api.py          # NinjaAPI root — mount app routers here
    │   ├── celery.py       # Celery app definition
    │   ├── urls.py         # URL root (/, /admin/, /api/)
    │   └── settings/
    │       ├── base.py     # Shared settings
    │       └── development.py
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
  │  HTTP (port 8000)
  ▼
ltlab-web  (Django + WhiteNoise)
  │  Celery task dispatch
  ▼
ltlab-redis  (broker + result backend)
  │
  ▼
ltlab-worker  (Celery worker — LTL checker)
  │
  ▼
ltlab-db  (PostgreSQL)
```

Migrations run automatically every time the `web` container starts (`entrypoint.sh` + `postStartCommand` in `devcontainer.json`).
