from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from .cache_key import make_cache_key
from .engine import analyze_lasso, check_ltl, cytoscape_to_kripke, validate_request

try:
    from celery.exceptions import SoftTimeLimitExceeded
except ImportError:
    SoftTimeLimitExceeded = None  # type: ignore[assignment,misc]


@shared_task
def run_ltl_check(kripke_graph: dict, ltl_formula: str) -> dict:
    """Celery task: check an LTL formula against a Cytoscape.js Kripke structure.

    Returns a dict ready for JSON serialisation:
      {"result": "satisfied", "formula": str, "kripke_graph": dict}
    or
      {"result": "violated", "formula": str, "kripke_graph": dict,
       "violation_kind": str, "violating_subformula": str,
       "trace": [{"state", "name", "props", "status", "highlight", "reason",
                  "in_cycle", "cycle_back"}, ...]}

    The formula and kripke_graph are echoed back so the polling status view can
    reconstruct the full rendering context from the Celery result alone.

    Raises ValueError (propagated as a Celery FAILURE) for invalid formulas,
    structural complexity cap violations, or malformed graphs.
    """
    try:
        # Primary DoS guard: validate before translate() which is expensive.
        validate_request(kripke_graph, ltl_formula)

        kripke, bdd_dict, spot_id_to_node = cytoscape_to_kripke(kripke_graph)
        result = check_ltl(kripke, bdd_dict, ltl_formula)

        if result["result"] == "violated":
            analysis = analyze_lasso(
                result.pop("prefix"),
                result.pop("cycle"),
                ltl_formula,
                kripke_graph,
                spot_id_to_node,
            )
            result["trace"]                = analysis["steps"]
            result["violation_kind"]       = analysis["violation_kind"]
            result["violating_subformula"] = analysis["violating_subformula"]

        result["formula"]      = ltl_formula
        result["kripke_graph"] = kripke_graph

        # Cache the completed result so repeat runs (same classroom burst) are
        # served instantly from Redis without queuing a task.
        try:
            ttl = getattr(settings, "RESULT_CACHE_TTL", 3600)
            cache.set(make_cache_key(ltl_formula, kripke_graph), result, ttl)
        except Exception:
            pass  # cache failure must never break the task

        return result

    except Exception as exc:  # noqa: BLE001
        # Re-raise SoftTimeLimitExceeded as a clean ValueError so the FAILURE
        # branch in verify_ltl_status can surface a user-friendly message.
        if SoftTimeLimitExceeded is not None and isinstance(exc, SoftTimeLimitExceeded):
            raise ValueError(
                "Verification timed out — please simplify the formula or graph."
            ) from exc
        raise
