#!/bin/bash
set -e
python manage.py migrate --fake-initial --noinput
python manage.py collectstatic --noinput
# gthread worker class (Google's recommended Cloud Run config): a single worker
# with multiple threads. SPOT holds the GIL during its C++ calls (measured), so
# threads do not give CPU parallelism — but a typical check is only a few
# milliseconds, so threads simply absorb request concurrency. CPU parallelism comes
# from Cloud Run autoscaling more instances, not from threads here.
#
# --timeout 30: runaway-check backstop (matches the old Celery hard time limit).
# A pathological check that hangs in SPOT's C++ code holds the GIL, so the gthread
# main thread can't send its heartbeat; the gunicorn arbiter then SIGKILLs and
# restarts the frozen worker (well under Cloud Run's 300s request ceiling). This is
# the only thing that can interrupt a C-level hang — a Python signal/alarm cannot.
#
# --max-requests recycles the worker periodically to bound SPOT BDD RSS growth.
# Cloud Run also OOM-kills + replaces an over-memory instance, so this is a cheap
# proactive trim on top of that platform recycle (replaces Celery max-memory-per-child).
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --threads "${GUNICORN_THREADS:-8}" \
    --timeout "${GUNICORN_TIMEOUT:-30}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-200}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-50}" \
    --log-level info
