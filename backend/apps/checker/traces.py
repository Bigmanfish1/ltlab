"""Verify a student-selected lasso against a Kripke graph and an LTL formula.

A lasso is prefix · cycle^ω over the graph's node ids. Path problems (not a
real path, wrong start state, unknown ids, empty cycle) are student data, not
exceptions — they come back as a user-facing message so the caller can record
an incorrect attempt with feedback. ValueError is reserved for system/teacher
problems (unparseable formula, complexity caps, oversized trace).
"""

from .engine import _LassoWord, _normalize_formula, _op_constants, _require_spot

try:
    import spot  # type: ignore[import]
except ImportError:  # pragma: no cover - mirrors engine's optional import
    spot = None

MAX_TRACE_STATES = 60


def _real_elements(graph):
    elements = graph.get("elements", {})
    nodes = [
        n for n in elements.get("nodes", [])
        if not n.get("data", {}).get("phantom")
    ]
    edges = [
        e for e in elements.get("edges", [])
        if not e.get("data", {}).get("phantom")
    ]
    return nodes, edges


def validate_lasso(graph, prefix, cycle):
    """Return a user-facing problem with the selected path, or None if valid.

    Valid means: every id names a real state, the first state overall is the
    initial state, consecutive states are joined by edges (including the
    prefix→cycle hand-off), and the cycle closes back onto its first state.
    """
    if not cycle:
        return "Select a repeating cycle — a lasso needs at least one looping state."

    trace = list(prefix) + list(cycle)
    if len(trace) > MAX_TRACE_STATES:
        raise ValueError(
            f"Trace is too long ({len(trace)} states) — at most {MAX_TRACE_STATES} are supported."
        )

    nodes, edges = _real_elements(graph)
    ids = {n["data"]["id"] for n in nodes}
    unknown = sorted(set(s for s in trace if s not in ids))
    if unknown:
        return "Unknown state(s) in the path: " + ", ".join(unknown) + "."

    initial = [n["data"]["id"] for n in nodes if n["data"].get("initial")]
    if not initial or trace[0] != initial[0]:
        return "The path must start at the initial state."

    edge_set = {
        (e["data"].get("source"), e["data"].get("target")) for e in edges
    }
    steps = list(zip(trace, trace[1:])) + [(trace[-1], cycle[0])]
    for src, tgt in steps:
        if (src, tgt) not in edge_set:
            return f"There is no transition from {src} to {tgt}."
    return None


def evaluate_lasso(graph, formula_str, prefix, cycle):
    """True iff the lasso word prefix·cycle^ω satisfies the formula at position 0.

    Assumes validate_lasso passed. Raises ValueError on an unparseable formula.
    """
    _require_spot()
    try:
        f = spot.formula(_normalize_formula(formula_str))
    except (SyntaxError, RuntimeError) as exc:
        raise ValueError(f"Invalid LTL formula: {exc}") from exc

    nodes, _ = _real_elements(graph)
    props_by_node = {
        n["data"]["id"]: list(n["data"].get("props", [])) for n in nodes
    }
    trace = list(prefix) + list(cycle)
    props_by_pos = [props_by_node[node_id] for node_id in trace]
    word = _LassoWord(props_by_pos, len(prefix), _op_constants())
    return word.holds(f, 0)
