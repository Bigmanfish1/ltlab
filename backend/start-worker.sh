#!/bin/bash
set -e
exec celery -A config.celery worker \
    --loglevel "${CELERY_LOG_LEVEL:-info}" \
    --concurrency "${CELERY_CONCURRENCY:-2}" \
    --max-tasks-per-child "${CELERY_MAX_TASKS_PER_CHILD:-50}" \
    --max-memory-per-child "${CELERY_MAX_MEMORY_PER_CHILD:-300000}"
