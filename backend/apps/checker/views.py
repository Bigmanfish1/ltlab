import json
import logging
import re

from django.core.cache import cache
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.middleware import supabase_login_required

from .cache_key import make_cache_key
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


def _error_response(request, message: str):
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


def _build_result_context(engine_result: dict, graph_json: str) -> dict:
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
        return _error_response(request, "No formula provided.")

    if len(formula) > MAX_FORMULA_CHARS:
        return _error_response(
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
        return _error_response(request, error)

    try:
        cached = cache.get(make_cache_key(formula, graph))
    except Exception:
        cached = None
    if cached is not None:
        context = _build_result_context(
            cached, json.dumps(cached.get("kripke_graph", graph))
        )
        return render(request, "sandbox/result.html", context)

    # Run the check synchronously — at the 15-state cap it is sub-millisecond,
    # so the request returns the rendered result directly. A ValueError carries
    # a clean, user-facing message from the engine (bad formula / complexity cap).
    # Any other exception (e.g. a SPOT/RuntimeError, or malformed input that slips
    # past validation) is logged and surfaced as a generic banner rather than an
    # unhandled 500 — the deleted async status view used to do this for the
    # Celery FAILURE path.
    try:
        engine_result = run_ltl_check(graph, formula)
    except ValueError as exc:
        return _error_response(request, str(exc))
    except Exception:
        logger.exception("LTL verification failed unexpectedly")
        return _error_response(
            request,
            "Verification was stopped — the formula or graph could not be processed.",
        )

    graph_json = json.dumps(engine_result["kripke_graph"])
    context = _build_result_context(engine_result, graph_json)
    return render(request, "sandbox/result.html", context)


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
