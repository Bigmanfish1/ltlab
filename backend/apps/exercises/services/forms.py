import json


def _json_field(request, name, default):
    try:
        value = json.loads(request.POST.get(name) or "")
    except json.JSONDecodeError:
        return default
    return value if isinstance(value, type(default)) else default


def parse_exercise_form(request):
    topic_id = request.POST.get("topic", "").strip()
    raw_parts = _json_field(request, "parts", [])
    parts = [
        {
            "id": str(p.get("id", "")).strip(),
            "prompt": str(p.get("prompt", "")).strip(),
            "formula": str(p.get("formula", "")).strip(),
            "hints": [
                str(h).strip()
                for h in (p.get("hints") if isinstance(p.get("hints"), list) else [])
                if str(h).strip()
            ][:3],
        }
        for p in raw_parts
        if isinstance(p, dict)
    ]
    return {
        "title": request.POST.get("title", "").strip(),
        "description": request.POST.get("description", "").strip(),
        "difficulty": request.POST.get("difficulty", "").strip(),
        "module_id": topic_id or None,
        "exercise_type": request.POST.get("exercise_type", "model_check").strip(),
        "hints": [request.POST.get(f"hint_{i}", "").strip() for i in (1, 2, 3)],
        "allowed_operators": _json_field(request, "allowed_operators", []),
        "declared_aps": [
            str(a).strip() for a in _json_field(request, "declared_aps", []) if str(a).strip()
        ],
        "parts": parts,
        "graph_data": request.POST.get("graph_data", "").strip(),
        # buchi_word's editor posts its own field so the two editors on the
        # builder page never collide on one name
        "automaton_data": request.POST.get("automaton_data", "").strip(),
        "target_formula": request.POST.get("target_formula", "").strip(),
        "ask_determinism": request.POST.get("ask_determinism") == "on",
    }
