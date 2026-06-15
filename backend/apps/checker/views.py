import json

from celery.result import AsyncResult
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.middleware import supabase_login_required

from .tasks import run_ltl_check


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


def _validate_graph(nodes: list) -> str | None:
    """Return an error message if the graph fails basic validation, else None."""
    if not nodes:
        return "Graph is empty — add at least one state."
    initial = [n for n in nodes if n["data"].get("initial")]
    if not initial:
        return "No initial state defined — select a state and press I to mark it."
    if len(initial) > 1:
        return "Multiple initial states found — exactly one is required."
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
        trace_str = " → ".join(
            n["data"]["id"] for n in nodes[:3]
        ) + (" → …" if node_count > 3 else "")
    else:
        steps = engine_result["trace"]
        trace_str = " → ".join(s["state"] for s in steps)
        trace_json = json.dumps(steps)

        # Only the states that genuinely break the formula are "violating".
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

    graph, nodes, node_count = _parse_graph(graph_json)

    if not formula:
        return _error_response(request, "No formula provided.")

    error = _validate_graph(nodes)
    if error:
        return _error_response(request, error)

    task = run_ltl_check.delay(graph, formula)
    return render(request, "sandbox/pending.html", {"task_id": task.id})


@supabase_login_required
@require_GET
def verify_ltl_status(request, task_id):
    result = AsyncResult(task_id)

    if result.state in ("PENDING", "STARTED", "RETRY"):
        return render(request, "sandbox/pending.html", {"task_id": task_id})

    if result.state == "FAILURE":
        exc = result.result
        return _error_response(request, f"Engine error: {exc}")

    # SUCCESS — the task echoes formula + kripke_graph back in its return dict
    engine_result = result.result
    graph_json = json.dumps(engine_result["kripke_graph"])
    context = _build_result_context(engine_result, graph_json)
    return render(request, "sandbox/result.html", context)


@supabase_login_required
@require_POST
def counterexample(request):
    formula = request.POST.get("formula", "")
    graph_data = request.POST.get("graph_data", "{}")
    trace_json = request.POST.get("trace_json", "[]")
    violating_states_json = request.POST.get("violating_states_json", "[]")
    violating_edges_json = request.POST.get("violating_edges_json", "[]")
    violating_subformula = request.POST.get("violating_subformula", "")
    violation_kind = request.POST.get("violation_kind", "safety")

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
            "violation_kind": violation_kind,
            "trace": trace,
            # Pre-serialised for safe embedding into JS variable declarations.
            "graph_json": graph_data,
            "trace_json": trace_json,
            "violating_states_json": violating_states_json,
            "violating_edges_json": violating_edges_json,
            "formula_json": json.dumps(formula),
            "violating_subformula_json": json.dumps(violating_subformula),
            "violation_kind_json": json.dumps(violation_kind),
        },
    )
