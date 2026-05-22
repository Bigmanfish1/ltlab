#!/bin/bash
set -e

# Only the web server should run migrations.
# The Celery worker shares the same image but must not migrate.
if [ "$1" != "celery" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
