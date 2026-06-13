#!/bin/bash
set -e
 
if [ "$1" != "celery" ] && [ "$RUN_MIGRATIONS" = "true" ]; then
    python manage.py migrate --fake-initial --noinput
fi
 
exec "$@"