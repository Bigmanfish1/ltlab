#!/bin/bash
set -e
python manage.py migrate --fake-initial --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gevent}" \
    --worker-connections "${GUNICORN_WORKER_CONNECTIONS:-1000}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --timeout "${GUNICORN_TIMEOUT:-30}" \
    --log-level info
