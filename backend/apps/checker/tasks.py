from celery import shared_task

from .engine import check_ltl, cytoscape_to_kripke, lasso_to_trace_steps


@shared_task
def run_ltl_check(kripke_graph: dict, ltl_formula: str) -> dict:
    """Celery task: check an LTL formula against a Cytoscape.js Kripke structure.

    Returns a dict ready for JSON serialisation:
      {"result": "satisfied"}
    or
      {"result": "violated",
       "trace": [{"state", "props", "ok", "highlight", "reason", "cycle_back"}, ...]}

    Raises ValueError (propagated as a Celery task failure) for invalid
    formulas or malformed graphs.
    """
    kripke, bdd_dict, spot_id_to_node = cytoscape_to_kripke(kripke_graph)
    result = check_ltl(kripke, bdd_dict, ltl_formula)

    if result["result"] == "violated":
        result["trace"] = lasso_to_trace_steps(
            result.pop("prefix"),
            result.pop("cycle"),
            ltl_formula,
            kripke_graph,
            spot_id_to_node,
        )

    return result
