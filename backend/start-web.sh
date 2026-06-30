#!/bin/bash
set -e
# Instance start runs gunicorn only: migrations run via the ltlab-migrate Job,
# static is collected at image build.
# gthread 1 worker / 8 threads: SPOT holds the GIL (measured) so threads add no
# CPU parallelism — checks are a few ms, bursts scale via Cloud Run instances.
# --timeout 30: out-of-process kill is the only way to interrupt a C-level hang
# in SPOT (GIL held → no heartbeat). No --max-requests: no measured leak.
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --threads "${GUNICORN_THREADS:-8}" \
    --timeout "${GUNICORN_TIMEOUT:-30}" \
    --log-level info
