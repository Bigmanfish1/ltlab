import json
import re

from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def _stub_highlight(ok: bool, props: list, violating_subformula: str, formula: str) -> str:
    """Return the formula token to highlight at this trace step.

    ok steps  → the first prop from this state that appears in the formula
                 (shows which antecedent is currently active/satisfied).
    bad steps → the failing subformula (e.g. "F grant").
    """
    if not ok:
        return violating_subformula
    for p in props:
        if p in formula:
            return p
    return ""


def _stub_reason(props: list, ok: bool, violating_subformula: str, formula: str) -> str:
    """Return a plain-English stub explanation for a trace step.

    Until the real LTL engine supplies per-step explanations this provides
    enough signal for the counterexample UI to be meaningful.
    """
    prop_str = "{" + ", ".join(props) + "}" if props else "∅"

    if ok:
        has_implication = "→" in formula or "->" in formula
        if has_implication and props:
            return (
                f"{prop_str} holds here — antecedent of the implication is "
                f"satisfied, so the consequent obligation is now active."
            )
        if has_implication and not props:
            return (
                "Antecedent does not hold here — implication is vacuously "
                "true, no obligation triggered."
            )
        return f"{prop_str} holds — formula condition is satisfied at this state."

    # Violation
    if violating_subformula:
        # e.g. "F grant" → eventual = "grant"
        eventual = re.sub(r"^F\s+", "", violating_subformula).strip()
        return (
            f"{prop_str} holds here, so the obligation '{violating_subformula}' "
            f"is triggered. However, '{eventual}' is never reached along any "
            f"successor path — the liveness requirement cannot be fulfilled."
        )
    return (
        f"The formula condition fails at this state. "
        f"Props holding: {prop_str}."
    )


@csrf_exempt
@require_POST
def verify_ltl(request):
    formula = request.POST.get("formula", "").strip()
    graph_json = request.POST.get("graph_data", "")

    try:
        graph = json.loads(graph_json) if graph_json else {}
        nodes = graph.get("elements", {}).get("nodes", [])
        node_count = len(nodes)
    except (json.JSONDecodeError, AttributeError):
        nodes = []
        node_count = 0

    if not formula:
        return render(request, "sandbox/result.html", {
            "status": "error",
            "message": "No formula provided.",
            "trace": None,
        })

    # Stub: formulas containing F are treated as violations so the
    # counterexample page can be exercised before the real engine lands.
    holds = "F" not in formula

    trace_json = "[]"
    violating_states_json = "[]"
    violating_edges_json = "[]"
    violating_subformula = ""
    trace_str = ""

    if holds:
        trace_str = "s0 → s1 → s2 → s0 (cycle)"
    else:
        # Build a stub trace from actual graph nodes when available.
        match = re.search(r"(F\s+\w+)", formula)
        violating_subformula = match.group(1).strip() if match else ""

        if nodes:
            node_ids = [n["data"]["id"] for n in nodes]
            trace_ids = node_ids[: min(4, len(node_ids))]
            half = max(1, len(trace_ids) // 2)
            stub_trace = []
            for i, nid in enumerate(trace_ids):
                node_data = next(
                    (n["data"] for n in nodes if n["data"]["id"] == nid), {}
                )
                ok = i < half
                props = node_data.get("props", [])
                stub_trace.append(
                    {
                        "state": nid,
                        "props": props,
                        "ok": ok,
                        "highlight": _stub_highlight(
                            ok, props, violating_subformula, formula
                        ),
                        "reason": _stub_reason(
                            props, ok, violating_subformula, formula
                        ),
                    }
                )
            violating_states = list({t["state"] for t in stub_trace})
            violating_edge_list = [
                [trace_ids[i - 1], trace_ids[i]] for i in range(1, len(trace_ids))
            ]
        else:
            stub_trace = [
                {
                    "state": "s0",
                    "props": [],
                    "ok": True,
                    "highlight": _stub_highlight(True, [], violating_subformula, formula),
                    "reason": _stub_reason([], True, violating_subformula, formula),
                },
                {
                    "state": "s1",
                    "props": [],
                    "ok": False,
                    "highlight": _stub_highlight(False, [], violating_subformula, formula),
                    "reason": _stub_reason([], False, violating_subformula, formula),
                },
            ]
            violating_states = ["s0", "s1"]
            violating_edge_list = [["s0", "s1"]]

        trace_str = " → ".join(t["state"] for t in stub_trace)
        trace_json = json.dumps(stub_trace)
        violating_states_json = json.dumps(violating_states)
        violating_edges_json = json.dumps(violating_edge_list)

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

    # Validate graph JSON — fall back to empty graph on error.
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
