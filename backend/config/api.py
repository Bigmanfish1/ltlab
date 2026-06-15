from django.conf import settings
from django.http import HttpResponse
from ninja import NinjaAPI

api = NinjaAPI(
    title="ltlab API",
    version="0.1.0",
    description=(
        "REST API for the ltlab educational tool — "
        "LTL model checking and Kripke structure verification."
    ),
)


@api.get("/health", tags=["system"])
def health(request):
    """Liveness probe — confirms the process is up."""
    return {"status": "ok"}


@api.get("/ready", tags=["system"])
def ready(request):
    """Readiness probe — confirms Redis and a Celery worker are reachable."""
    try:
        import redis
        r = redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2)
        r.ping()
    except Exception:
        return HttpResponse(status=503)

    try:
        from config.celery import app as celery_app
        if not celery_app.control.ping(timeout=1):
            return HttpResponse(status=503)
    except Exception:
        return HttpResponse(status=503)

    return {"status": "ok"}
