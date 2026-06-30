#!/bin/bash
set -e
# Instance start runs gunicorn only. Migrations run once via the ltlab-migrate
# Cloud Run Job in the deploy pipeline; static is collected at image build time.
#
# gthread, 1 worker / 8 threads: SPOT holds the GIL (measured) so threads give no
# CPU parallelism, but checks are a few ms so threads absorb concurrency; burst
# parallelism comes from Cloud Run autoscaling, not threads.
# --timeout 30: out-of-process kill of a worker frozen in SPOT's C++ (GIL held →
# no heartbeat) — the only thing that can interrupt a C-level hang.
# No --max-requests: no measured leak (~5MB/500) and Cloud Run OOM-recycles, so
# recycling the lone worker would only re-pay the import cost.
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --threads "${GUNICORN_THREADS:-8}" \
    --timeout "${GUNICORN_TIMEOUT:-30}" \
    --log-level info
