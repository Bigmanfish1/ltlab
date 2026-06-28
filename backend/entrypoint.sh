#!/bin/bash
set -e
 
if [ "$RUN_MIGRATIONS" = "true" ]; then
    python manage.py migrate --fake-initial --noinput
fi
 
exec "$@"