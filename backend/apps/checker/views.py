import json
import logging
import re

from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.middleware import supabase_login_required

from .engine import formula_relationship
from .tasks import run_ltl_check

logger = logging.getLogger(__name__)

MAX_FORMULA_CHARS = 512
MAX_NODES = 100
MAX_EDGES = 400

# Prop names must be valid identifiers and not clash with SPOT's temporal
# operators (X F G U R W M) or Boolean constants (true false tt ff).
_PROP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_PROP_NAMES = frozenset(
    {
        "X",
        "F",
        "G",
        "U",
        "R",
        "W",
        "M",
        "true",
        "false",
        "tt",
        "ff",
    }
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def error_response(request, message: str):
    """Render the result drawer with a grey error banner."""
    return render(
        request,
        "sandbox/result.html",
        {
            "status": "error",
            "message": message,
        },
    )


def _parse_graph(graph_json: str) -> tuple:
    """Parse Cytoscape JSON; return (graph, real_nodes, node_count)."""
    try:
        graph = json.loads(graph_json) if graph_json else {}
    except json.JSONDecodeError:
        graph = {}
    elements = graph.get("elements", {})
    nodes = [
        n for n in elements.get("nodes", []) if not n.get("data", {}).get("phantom")
    ]
    return graph, nodes, len(nodes)


def _validate_graph(nodes: list, edges: list | None = None) -> str | None:
    """Return an error message if the graph fails validation, else None."""
    if not nodes:
        return "Graph is empty — add at least one state."

    if len(nodes) > MAX_NODES:
        return (
            f"Graph has {len(nodes)} states — the sandbox supports at most "
            f"{MAX_NODES} states."
        )

    initial = [n for n in nodes if n["data"].get("initial")]
    if not initial:
        return "No initial state defined — select a state and press I to mark it."
    if len(initial) > 1:
        return "Multiple initial states found — exactly one is required."

    if edges is not None:
        if len(edges) > MAX_EDGES:
            return (
                f"Graph has {len(edges)} transitions — the sandbox supports at most "
                f"{MAX_EDGES} transitions."
            )
        seen: set[tuple] = set()
        for e in edges:
            key = (e["data"].get("source"), e["data"].get("target"))
            if key in seen:
                return "Duplicate transition found — only one transition is allowed between any two states."
            seen.add(key)

    # Validate proposition names across all nodes.
    for n in nodes:
        for prop in n["data"].get("props", []):
            if not _PROP_NAME_RE.match(prop):
                return (
                    f'Proposition name "{prop}" is invalid — names must start '
                    "with a letter or underscore and contain only letters, digits, "
                    "and underscores (e.g. p, req, my_prop)."
                )
            if prop in _RESERVED_PROP_NAMES:
                return (
                    f'Proposition name "{prop}" is a reserved LTL operator — '
                    "choose a different name (e.g. p, req, my_prop)."
                )

    return None


def build_result_context(engine_result: dict, graph_json: str) -> dict:
    """Translate a run_ltl_check result dict into a template context dict."""
    formula = engine_result["formula"]
    graph, nodes, node_count = _parse_graph(graph_json)

    holds = engine_result["result"] == "satisfied"

    trace_json = "[]"
    violating_states_json = "[]"
    violating_edges_json = "[]"
    violating_subformula = ""
    violation_kind = ""
    trace_str = ""

    if holds:
        trace_str = " → ".join(n["data"]["id"] for n in nodes[:3]) + (
            " → …" if node_count > 3 else ""
        )
    else:
        steps = engine_result["trace"]
        trace_str = " → ".join(s["state"] for s in steps)
        trace_json = json.dumps(steps)

        violating_states = []
        for s in steps:
            if s.get("status") == "violating" and s["state"] not in violating_states:
                violating_states.append(s["state"])
        violating_states_json = json.dumps(violating_states)

        edge_pairs = [
            [steps[i]["state"], steps[i + 1]["state"]]
            for i in range(len(steps) - 1)
            if steps[i].get("status") == "violating"
            and steps[i + 1].get("status") == "violating"
        ]
        violating_edges_json = json.dumps(edge_pairs)

        violating_subformula = engine_result.get("violating_subformula", "")
        violation_kind = engine_result.get("violation_kind", "safety")

    return {
        "status": "holds" if holds else "violated",
        "formula": formula,
        "trace": trace_str,
        "node_count": node_count,
        "graph_data": graph_json,
        "trace_json": trace_json,
        "violating_states_json": violating_states_json,
        "violating_edges_json": violating_edges_json,
        "violating_subformula": violating_subformula,
        "violation_kind": violation_kind,
    }


# ── Views ─────────────────────────────────────────────────────────────────────


@csrf_exempt
@supabase_login_required
@require_POST
def verify_ltl(request):
    formula = request.POST.get("formula", "").strip()
    graph_json = request.POST.get("graph_data", "")

    if not formula:
        return error_response(request, "No formula provided.")

    if len(formula) > MAX_FORMULA_CHARS:
        return error_response(
            request,
            f"Formula is too long ({len(formula)} characters) — the sandbox "
            f"supports at most {MAX_FORMULA_CHARS} characters.",
        )

    graph, nodes, node_count = _parse_graph(graph_json)

    real_edges = [
        e
        for e in graph.get("elements", {}).get("edges", [])
        if not e.get("data", {}).get("phantom")
    ]
    error = _validate_graph(nodes, real_edges)
    if error:
        return error_response(request, error)

    # Synchronous — a check is a few ms. ValueError carries a clean user-facing
    # message (bad formula / complexity cap); any other exception is logged and
    # shown as a generic banner instead of a 500.
    try:
        engine_result = run_ltl_check(graph, formula)
    except ValueError as exc:
        return error_response(request, str(exc))
    except Exception:
        logger.exception("LTL verification failed unexpectedly")
        return error_response(
            request,
            "Verification was stopped — the formula or graph could not be processed.",
        )

    graph_json = json.dumps(engine_result["kripke_graph"])
    context = build_result_context(engine_result, graph_json)
    return render(request, "sandbox/result.html", context)


# Plain-English summary per relationship, keyed for the result template.
# (Project style: no em dashes in user-facing prose.)
_EQ_SUMMARY = {
    "equivalent": "Both accept exactly the same runs, so either one can stand in "
                  "for the other.",
    "a_stronger": "Every run that satisfies A also satisfies B, but not the other "
                  "way round. A asks for more.",
    "b_stronger": "Every run that satisfies B also satisfies A, but not the other "
                  "way round. B asks for more.",
    "incomparable": "Neither one covers the other. Each accepts runs the other "
                    "rejects.",
}

# (border/text color, banner background, title) per relationship.
_EQ_VERDICT_STYLE = {
    "equivalent":   ("#AEFC00", "#0D1F0D", "Equivalent"),
    "a_stronger":   ("#E0A030", "#1A160A", "One-way implication"),
    "b_stronger":   ("#E0A030", "#1A160A", "One-way implication"),
    "incomparable": ("#FF4D00", "#1F0D0D", "Not equivalent"),
}


def _eq_error(request, message: str):
    """Render the equivalence result panel with a grey error banner."""
    return render(
        request,
        "sandbox/equivalence_result.html",
        {"status": "error", "message": message},
    )


@supabase_login_required
@require_POST
def equivalence_check(request):
    formula_a = request.POST.get("formula_a", "").strip()
    formula_b = request.POST.get("formula_b", "").strip()

    if not formula_a or not formula_b:
        return _eq_error(request, "Enter a formula in both A and B to compare them.")

    for label, value in (("A", formula_a), ("B", formula_b)):
        if len(value) > MAX_FORMULA_CHARS:
            return _eq_error(
                request,
                f"Formula {label} is too long ({len(value)} characters) — the "
                f"explorer supports at most {MAX_FORMULA_CHARS} characters.",
            )

    try:
        result = formula_relationship(formula_a, formula_b)
    except ValueError as exc:
        return _eq_error(request, str(exc))
    except Exception:
        logger.exception("Equivalence check failed unexpectedly")
        return _eq_error(
            request,
            "Comparison was stopped — the formulas could not be processed.",
        )

    # Build a fresh context (never mutate the lru_cached result). Each witness
    # gets a unique editor id and its elements serialized for the read-only graph;
    # the trace stays a Python object for json_script embedding.
    witnesses = []
    for i, w in enumerate(result["witnesses"]):
        eid = f"eq-witness-{i}"
        witnesses.append({
            "eid": eid,
            "trace_eid": f"{eid}-eq-trace",
            "satisfies": w["satisfies"],
            "violates": w["violates"],
            "a_accepts": w["satisfies"] == "A",
            "b_accepts": w["satisfies"] == "B",
            "violation_kind": w.get("violation_kind", ""),
            "divergence": w.get("divergence", ""),
            "elements_json": json.dumps(w["elements"]),
            "trace": w["trace"],
        })

    # Equivalent formulas have no distinguishing model; instead we show one
    # behaviour they both accept (or nothing, if both are contradictions).
    shared = result.get("shared_example")
    shared_example = None
    if shared:
        eid = "eq-shared"
        shared_example = {
            "eid": eid,
            "trace_eid": f"{eid}-eq-trace",
            "a_accepts": True,
            "b_accepts": True,
            "elements_json": json.dumps(shared["elements"]),
            "trace": shared["trace"],
        }

    # (label, formula text, facts dict) rows for the insight strip.
    facts_pair = [
        ("A", formula_a, result.get("facts_a") or {}),
        ("B", formula_b, result.get("facts_b") or {}),
    ]

    # Interactive timeline data: the syntax trees of A and B plus a set of
    # preset traces (the distinguishing witnesses, or one both accept). The
    # browser evaluates every subformula over whichever trace the student edits.
    def _cols_loop(trace):
        cols = [list(s["props"]) for s in trace]
        loop = next((i for i, s in enumerate(trace) if s.get("cycle_start")), 0)
        return cols, loop

    presets = []
    for w in result["witnesses"]:
        cols, loop = _cols_loop(w["trace"])
        presets.append({
            "name": f"{w['satisfies']} accepts it, {w['violates']} rejects it",
            "cols": cols, "loop": loop, "distinguishing": True,
        })
    if shared:
        cols, loop = _cols_loop(shared["trace"])
        presets.append({
            "name": "both accept this trace", "cols": cols, "loop": loop,
            "distinguishing": False,
        })
    if not presets:
        # Contradictions: no accepting trace exists, but the student can still
        # build one and watch both formulas reject it. Seed a short empty trace.
        presets.append({
            "name": "empty trace", "cols": [[], []], "loop": 0,
            "distinguishing": False,
        })

    timeline = {
        "aps": result.get("aps", []),
        "formulas": [
            {"label": "A", "text": formula_a, "ast": result["ast_a"]},
            {"label": "B", "text": formula_b, "ast": result["ast_b"]},
        ],
        "presets": presets,
    }

    relationship = result["relationship"]
    vcolor, vbg, vtitle = _EQ_VERDICT_STYLE.get(
        relationship, ("#6B6B6B", "#111111", "Result")
    )

    return render(
        request,
        "sandbox/equivalence_result.html",
        {
            "status": "result",
            "relationship": relationship,
            "glyph": result["glyph"],
            "summary": _EQ_SUMMARY.get(relationship, ""),
            "verdict_color": vcolor,
            "verdict_bg": vbg,
            "verdict_title": vtitle,
            "formula_a": formula_a,
            "formula_b": formula_b,
            "facts_pair": facts_pair,
            "witnesses": witnesses,
            "shared_example": shared_example,
            "shared_note": result.get("note", ""),
            "timeline": timeline,
        },
    )


@supabase_login_required
@require_POST
def counterexample(request):
    formula = request.POST.get("formula", "")
    graph_data = request.POST.get("graph_data", "{}")
    trace_json_raw = request.POST.get("trace_json", "[]")
    violating_states_raw = request.POST.get("violating_states_json", "[]")
    violating_edges_raw = request.POST.get("violating_edges_json", "[]")
    violating_subformula = request.POST.get("violating_subformula", "")
    violation_kind = request.POST.get("violation_kind", "safety")

    # Parse all incoming JSON to Python objects; fall back gracefully.
    try:
        trace = json.loads(trace_json_raw)
    except json.JSONDecodeError:
        trace = []

    try:
        graph = json.loads(graph_data)
    except json.JSONDecodeError:
        graph = {}

    try:
        violating_states = json.loads(violating_states_raw)
    except json.JSONDecodeError:
        violating_states = []

    try:
        violating_edges = json.loads(violating_edges_raw)
    except json.JSONDecodeError:
        violating_edges = []

    # Pass Python objects to the template; json_script is used there for safe
    # JS embedding (escapes </script>, U+2028/U+2029, etc.).
    return render(
        request,
        "sandbox/counterexample.html",
        {
            "formula": formula,
            "violating_subformula": violating_subformula,
            "violation_kind": violation_kind,
            "trace": trace,
            "graph_obj": graph,
            "trace_obj": trace,
            "violating_states_obj": violating_states,
            "violating_edges_obj": violating_edges,
            "formula_str": formula,
            "violating_subformula_str": violating_subformula,
            "violation_kind_str": violation_kind,
        },
    )
