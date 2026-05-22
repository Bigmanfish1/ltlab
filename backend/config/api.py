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
    return {"status": "ok", "service": "ltlab"}
