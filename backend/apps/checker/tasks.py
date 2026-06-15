from celery import shared_task

from .engine import analyze_lasso, check_ltl, cytoscape_to_kripke


@shared_task
def run_ltl_check(kripke_graph: dict, ltl_formula: str) -> dict:
    """Celery task: check an LTL formula against a Cytoscape.js Kripke structure.

    Returns a dict ready for JSON serialisation:
      {"result": "satisfied", "formula": str, "kripke_graph": dict}
    or
      {"result": "violated", "formula": str, "kripke_graph": dict,
       "violation_kind": str, "violating_subformula": str,
       "trace": [{"state", "props", "status", "highlight", "reason",
                  "in_cycle", "cycle_back"}, ...]}

    The formula and kripke_graph are echoed back so the polling status view can
    reconstruct the full rendering context from the Celery result alone, without
    needing a separate cache store.

    Raises ValueError (propagated as a Celery task failure) for invalid
    formulas or malformed graphs.
    """
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
        result["trace"] = analysis["steps"]
        result["violation_kind"] = analysis["violation_kind"]
        result["violating_subformula"] = analysis["violating_subformula"]

    result["formula"] = ltl_formula
    result["kripke_graph"] = kripke_graph
    return result
