from django.db import connection
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
    """Readiness probe — confirms the database is reachable.

    The LTL check runs in-process (no queue/worker), so the database is the
    only external dependency to verify here.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse(status=503)

    return {"status": "ok"}
