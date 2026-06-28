from django.conf import settings
from django.core.cache import cache

from .cache_key import make_cache_key
from .engine import analyze_lasso, check_ltl, cytoscape_to_kripke, validate_request


def run_ltl_check(kripke_graph: dict, ltl_formula: str) -> dict:
    """Check an LTL formula against a Cytoscape.js Kripke structure.

    Runs synchronously inside the request — at the sandbox's 15-state cap a
    check is sub-millisecond, so there is no benefit to a background queue.
    Cloud Run absorbs concurrent bursts by autoscaling instances.

    Returns a dict ready for JSON serialisation:
      {"result": "satisfied", "formula": str, "kripke_graph": dict}
    or
      {"result": "violated", "formula": str, "kripke_graph": dict,
       "violation_kind": str, "violating_subformula": str,
       "trace": [{"state", "name", "props", "status", "highlight", "reason",
                  "in_cycle", "cycle_back"}, ...]}

    The formula and kripke_graph are echoed back so the caller can build the
    full rendering context from the return dict alone.

    Raises ValueError for invalid formulas, structural complexity cap
    violations, or malformed graphs — the view surfaces these to the user.
    """
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
    # served instantly. LocMemCache is per-instance on Cloud Run — a harmless
    # micro-optimisation, never shared state the app depends on.
    try:
        ttl = getattr(settings, "RESULT_CACHE_TTL", 3600)
        cache.set(make_cache_key(ltl_formula, kripke_graph), result, ttl)
    except Exception:
        pass  # cache failure must never break the check

    return result
