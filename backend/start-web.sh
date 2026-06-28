#!/bin/bash
set -e
python manage.py migrate --fake-initial --noinput
python manage.py collectstatic --noinput
# gthread worker class (Google's recommended Cloud Run config): a single worker
# with multiple threads. SPOT holds the GIL during its C++ calls (measured), so
# threads do not give CPU parallelism — but each 15-state check is sub-millisecond,
# so threads simply absorb request concurrency. CPU parallelism for bursts comes
# from Cloud Run autoscaling more instances, not from threads here.
# --max-requests recycles the worker periodically to bound SPOT BDD RSS growth
# (the replacement for the old Celery max-memory-per-child recycling).
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --threads "${GUNICORN_THREADS:-8}" \
    --timeout "${GUNICORN_TIMEOUT:-0}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-500}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-50}" \
    --log-level info
