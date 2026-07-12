from .engine import analyze_lasso, check_ltl, cytoscape_to_kripke, validate_request
from .equivalence import check_equivalence, validate_formula_submission


def run_ltl_check(kripke_graph: dict, ltl_formula: str) -> dict:
    """Check an LTL formula against a Cytoscape.js Kripke structure.

    Runs synchronously in the request — a typical check is a few ms (measured on
    small graphs) and the validation caps bound the work (≤100 states; ≤8 APs /
    ≤10 temporal operators / ≤40 formula nodes), so a queue would only add
    latency. Cloud Run absorbs bursts by autoscaling instances.

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

    return result


def run_equivalence_check(target: str, submitted: str, declared_aps: list[str]) -> dict:
    """Grade a submitted formula against a hidden target by language equivalence.

    The submission is validated against the caps and the exercise's declared AP
    list first (ValueError on violation, surfaced to the user). The target is
    teacher data validated at publish time, so a target parse failure here also
    raises ValueError rather than being masked as a wrong answer.

    Returns {"equivalent": bool, "formula": str}.
    """
    validate_formula_submission(submitted, declared_aps)
    return {
        "equivalent": check_equivalence(target, submitted),
        "formula": submitted,
    }
