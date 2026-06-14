#!/bin/bash
set -e
python manage.py migrate --fake-initial --noinput
python manage.py collectstatic --noinput
celery -A config.celery worker --loglevel=info &
exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
