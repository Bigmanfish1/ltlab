import json

from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .engine import check_ltl, cytoscape_to_kripke, lasso_to_trace_steps


# ── Helpers ───────────────────────────────────────────────────────────────────

def _error_response(request, message: str):
    """Render the result drawer with a grey error banner."""
    return render(request, "sandbox/result.html", {
        "status": "error",
        "message": message,
    })


def _parse_graph(graph_json: str) -> tuple:
    """Parse Cytoscape JSON; return (graph, real_nodes, node_count)."""
    try:
        graph = json.loads(graph_json) if graph_json else {}
    except json.JSONDecodeError:
        graph = {}
    elements = graph.get("elements", {})
    nodes = [
        n for n in elements.get("nodes", [])
        if not n.get("data", {}).get("phantom")
    ]
    return graph, nodes, len(nodes)


# ── Views ─────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def verify_ltl(request):
    formula = request.POST.get("formula", "").strip()
    graph_json = request.POST.get("graph_data", "")

    graph, nodes, node_count = _parse_graph(graph_json)

    # ── Server-side validation (mirrors JS pre-submit checks) ─────────────────
    if not formula:
        return _error_response(request, "No formula provided.")

    if node_count == 0:
        return _error_response(request, "Graph is empty — add at least one state.")

    initial_nodes = [n for n in nodes if n["data"].get("initial")]
    if len(initial_nodes) == 0:
        return _error_response(
            request,
            "No initial state defined — select a state and press I to mark it.",
        )
    if len(initial_nodes) > 1:
        return _error_response(
            request,
            "Multiple initial states found — exactly one is required.",
        )

    # ── Run SPOT ──────────────────────────────────────────────────────────────
    try:
        kripke, bdd_dict, spot_id_to_node = cytoscape_to_kripke(graph)
        engine_result = check_ltl(kripke, bdd_dict, formula)
    except ValueError as exc:
        return _error_response(request, str(exc))
    except RuntimeError as exc:
        return _error_response(request, f"Engine error: {exc}")

    holds = engine_result["result"] == "satisfied"

    trace_json = "[]"
    violating_states_json = "[]"
    violating_edges_json = "[]"
    violating_subformula = ""
    trace_str = ""

    if holds:
        trace_str = " → ".join(
            n["data"]["id"] for n in nodes[:3]
        ) + (" → …" if node_count > 3 else "")
    else:
        steps = lasso_to_trace_steps(
            engine_result["prefix"],
            engine_result["cycle"],
            formula,
            graph,
            spot_id_to_node,
        )

        trace_str = " → ".join(s["state"] for s in steps)
        trace_json = json.dumps(steps)

        # Derive violating state/edge sets for the counterexample graph overlay.
        cycle_states = {s["state"] for s in steps if not s["ok"]}
        violating_states_json = json.dumps(list(cycle_states))

        edge_pairs = [
            [steps[i]["state"], steps[i + 1]["state"]]
            for i in range(len(steps) - 1)
            if not steps[i]["ok"] and not steps[i + 1]["ok"]
        ]
        violating_edges_json = json.dumps(edge_pairs)

        cycle_steps = [s for s in steps if not s["ok"]]
        violating_subformula = cycle_steps[0]["highlight"] if cycle_steps else ""

    context = {
        "status": "holds" if holds else "violated",
        "formula": formula,
        "trace": trace_str,
        "node_count": node_count,
        "graph_data": graph_json,
        "trace_json": trace_json,
        "violating_states_json": violating_states_json,
        "violating_edges_json": violating_edges_json,
        "violating_subformula": violating_subformula,
    }
    return render(request, "sandbox/result.html", context)


@csrf_exempt
@require_POST
def counterexample(request):
    formula = request.POST.get("formula", "")
    graph_data = request.POST.get("graph_data", "{}")
    trace_json = request.POST.get("trace_json", "[]")
    violating_states_json = request.POST.get("violating_states_json", "[]")
    violating_edges_json = request.POST.get("violating_edges_json", "[]")
    violating_subformula = request.POST.get("violating_subformula", "")

    try:
        trace = json.loads(trace_json)
    except json.JSONDecodeError:
        trace = []

    try:
        json.loads(graph_data)
    except json.JSONDecodeError:
        graph_data = "{}"

    return render(
        request,
        "sandbox/counterexample.html",
        {
            "formula": formula,
            "violating_subformula": violating_subformula,
            "trace": trace,
            # Pre-serialised for safe embedding into JS variable declarations.
            "graph_json": graph_data,
            "trace_json": trace_json,
            "violating_states_json": violating_states_json,
            "violating_edges_json": violating_edges_json,
            "formula_json": json.dumps(formula),
            "violating_subformula_json": json.dumps(violating_subformula),
        },
    )
