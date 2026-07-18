from .buchi import (
    check_buchi_equivalence,
    is_deterministic,
    target_automaton_states,
    word_accepted,
)
from .engine import analyze_lasso, check_ltl, cytoscape_to_kripke, validate_request
from .equivalence import (
    check_equivalence,
    formulas_jointly_satisfiable,
    validate_formula_submission,
)
from .traces import evaluate_lasso, validate_lasso


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


def run_trace_check(graph: dict, formula: str, prefix: list[str], cycle: list[str]) -> dict:
    """Verify a student-selected lasso path and evaluate the formula on it.

    Returns {"path_ok": bool, "path_error": str | None, "holds": bool | None}.
    A broken path (not a real path of the graph, wrong start, open cycle) is
    student data: path_ok=False with a user-facing path_error and holds=None.
    ValueError is raised only for system/teacher problems — unparseable
    formula, complexity caps, malformed graph, oversized trace.
    """
    validate_request(graph, formula)
    error = validate_lasso(graph, prefix, cycle)
    if error is not None:
        return {"path_ok": False, "path_error": error, "holds": None}
    return {
        "path_ok": True,
        "path_error": None,
        "holds": evaluate_lasso(graph, formula, prefix, cycle),
    }


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


def run_model_solvable_check(formulas: list[str], declared_aps: list[str]) -> dict:
    """Whether some Kripke structure can satisfy every required formula at once.

    Backs the build_kripke publish gate and the builder Test button. Raises
    ValueError on an invalid formula. Returns {"solvable": bool}.
    """
    return {"solvable": formulas_jointly_satisfiable(formulas, declared_aps)}


def run_buchi_equivalence_check(automaton: dict, target: str, declared_aps: list[str]) -> dict:
    """Grade a drawn Büchi automaton against a target LTL by language equivalence.

    A malformed automaton (bad label, no initial state, over cap) is a
    teacher/system fault and raises ValueError. A well-formed but wrong student
    automaton returns {"equivalent": False}.
    """
    return {"equivalent": check_buchi_equivalence(automaton, target, declared_aps)}


def run_buchi_determinism_check(automaton: dict, declared_aps: list[str]) -> dict:
    """Whether the drawn automaton is deterministic. Returns {"deterministic": bool}."""
    return {"deterministic": is_deterministic(automaton, declared_aps)}


def run_buchi_word_check(automaton: dict, word: str, declared_aps: list[str]) -> dict:
    """Grade a typed lasso word against a fixed automaton by membership.

    The automaton is teacher data (raises on a bad one); the word is student
    data. Returns {"accepted": bool, "word_error": str | None} — a rejected or
    unreadable word is accepted=False with a user-facing word_error.
    """
    accepted, message = word_accepted(automaton, word, declared_aps)
    return {"accepted": accepted, "word_error": message or None}


def run_buchi_target_check(target: str, declared_aps: list[str]) -> dict:
    """Publish gate / builder Test for a buchi_construct target LTL formula.

    Validates the target parses and stays within the formula caps and the
    declared alphabet (ValueError on violation), and reports the target
    automaton's state count for teacher feedback. Returns {"states": int}.
    """
    validate_formula_submission(target, declared_aps)
    return {"states": target_automaton_states(target, declared_aps)}
