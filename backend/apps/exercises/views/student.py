from django.shortcuts import get_object_or_404, render

from apps.accounts.middleware import supabase_login_required

from ..constants import BUILDER_OPERATORS, EXERCISE_TYPE_BADGES, OPERATOR_LABELS
from ..models import Attempt
from ..services import _elements_json, graph_aps, solved_exercise_ids
from .common import published_exercises


@supabase_login_required
def exercises(request):
    all_published = list(published_exercises())
    solved = solved_exercise_ids(request.profile)
    exercises_data = []
    for exercise in all_published:
        attempt_count = Attempt.objects.filter(exercise=exercise, student=request.profile).count()
        exercises_data.append({
            'exercise': exercise,
            'is_completed': exercise.id in solved,
            'attempt_count': attempt_count,
            'best_attempt': None,
        })

    return render(request, 'exercises/student/list.html', {'exercises_data': exercises_data})


def _exercise_nav(exercise_id):
    all_exercises = list(published_exercises().only('id', 'position'))
    current_index = next((i for i, ex in enumerate(all_exercises) if ex.id == exercise_id), 0)
    prev_exercise = all_exercises[current_index - 1] if current_index > 0 else None
    next_exercise = all_exercises[current_index + 1] if current_index < len(all_exercises) - 1 else None
    return current_index + 1, prev_exercise, next_exercise


def _operator_buttons(exercise):
    # only the operators the teacher allowed (None = legacy exercise = all)
    allowed = exercise.allowed_operators if exercise.allowed_operators is not None else BUILDER_OPERATORS
    buttons = []
    for op in BUILDER_OPERATORS:
        if op not in allowed:
            continue
        buttons.append({"op": op, "label": OPERATOR_LABELS.get(op, op)})
        if op == "∨":
            buttons.append({"op": "|", "label": "Or"})
    return buttons


@supabase_login_required
def exercise_canvas(request, exercise_id):
    """Exercise page — dispatches to the type-specific template."""
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    if exercise.exercise_type == "english_to_formula":
        return _part_canvas(
            request, exercise, "exercises/student/english.html",
            declared_aps=list(exercise.declared_aps or []),
            operator_buttons=_operator_buttons(exercise),
        )
    if exercise.exercise_type == "path_exhibit":
        return _part_canvas(request, exercise, "exercises/student/path.html")
    if exercise.exercise_type == "judge":
        return _part_canvas(request, exercise, "exercises/student/judge.html")
    if exercise.exercise_type == "build_kripke":
        return _build_kripke_canvas(request, exercise)
    if exercise.exercise_type == "buchi_construct":
        return _buchi_construct_canvas(request, exercise)
    if exercise.exercise_type == "buchi_word":
        return _buchi_word_canvas(request, exercise)

    attempts = Attempt.objects.filter(
        exercise=exercise, student=request.profile
    ).order_by("-created_at")
    is_completed = Attempt.objects.filter(
        exercise=exercise, student=request.profile, is_correct=True
    ).exists()

    exercise_number, prev_exercise, next_exercise = _exercise_nav(exercise_id)

    context = {
        'exercise': exercise,
        'exercise_number': exercise_number,
        'elements_json': _elements_json(exercise.kripke_structure),
        'operator_buttons': _operator_buttons(exercise),
        'declared_aps': graph_aps(exercise.kripke_structure),
        'attempts': attempts,
        'is_completed': is_completed,
        'prev_exercise': prev_exercise,
        'next_exercise': next_exercise,
        'type_badge': EXERCISE_TYPE_BADGES.get(exercise.exercise_type, ""),
    }
    return render(request, 'exercises/student/model_check.html', context)


def _part_rows(exercise, student):
    parts = list(exercise.parts.all())
    correct_part_ids = set(
        Attempt.objects.filter(
            exercise=exercise, student=student,
            is_correct=True, part__isnull=False,
        ).values_list("part_id", flat=True)
    )
    return [
        {
            "part": p,
            "number": i,
            "solved": p.id in correct_part_ids,
            "input_id": f"formula-{p.id}",
        }
        for i, p in enumerate(parts, start=1)
    ]


def _part_canvas(request, exercise, template, **extra):
    part_rows = _part_rows(exercise, request.profile)
    exercise_number, prev_exercise, next_exercise = _exercise_nav(exercise.id)
    context = {
        "exercise": exercise,
        "exercise_number": exercise_number,
        "part_rows": part_rows,
        "elements_json": _elements_json(exercise.kripke_structure),
        "is_completed": bool(part_rows) and all(r["solved"] for r in part_rows),
        "prev_exercise": prev_exercise,
        "next_exercise": next_exercise,
        "type_badge": EXERCISE_TYPE_BADGES.get(exercise.exercise_type, ""),
        **extra,
    }
    return render(request, template, context)


def _build_kripke_canvas(request, exercise):
    """Student page for build_kripke — editable editor plus the required formulas."""
    part_rows = _part_rows(exercise, request.profile)
    exercise_number, prev_exercise, next_exercise = _exercise_nav(exercise.id)
    # restore the last submitted graph so a reload doesn't revert to the demo
    last = (
        Attempt.objects.filter(exercise=exercise, student=request.profile)
        .order_by("-created_at")
        .first()
    )
    last_graph = last.answer.get("graph") if last and isinstance(last.answer, dict) else None
    context = {
        "exercise": exercise,
        "exercise_number": exercise_number,
        "part_rows": part_rows,
        "declared_aps": list(exercise.declared_aps or []),
        "elements_json": _elements_json(last_graph),
        # completion means one submitted model satisfied every requirement
        "is_completed": Attempt.objects.filter(
            exercise=exercise, student=request.profile, is_correct=True
        ).exists(),
        "prev_exercise": prev_exercise,
        "next_exercise": next_exercise,
        "type_badge": EXERCISE_TYPE_BADGES.get(exercise.exercise_type, ""),
    }
    return render(request, "exercises/student/build_kripke.html", context)


def _buchi_construct_canvas(request, exercise):
    """Student page for buchi_construct — draw a Büchi automaton for the target."""
    exercise_number, prev_exercise, next_exercise = _exercise_nav(exercise.id)
    # restore the last submitted automaton so a reload keeps the student's work
    last = (
        Attempt.objects.filter(exercise=exercise, student=request.profile)
        .order_by("-created_at")
        .first()
    )
    last_automaton = (
        last.answer.get("automaton") if last and isinstance(last.answer, dict) else None
    )
    context = {
        "exercise": exercise,
        "exercise_number": exercise_number,
        "declared_aps": list(exercise.declared_aps or []),
        # start blank for a fresh student (the editor's demo is an answer-shaped
        # automaton); restore their saved drawing on return
        "elements_json": _elements_json(last_automaton) or "[]",
        "ask_determinism": exercise.ask_determinism,
        "last_determinism": (
            (last.answer or {}).get("determinism", "") if last else ""
        ),
        "autosave_key": f"buchi:{request.profile.id}:{exercise.id}",
        "is_completed": Attempt.objects.filter(
            exercise=exercise, student=request.profile, is_correct=True
        ).exists(),
        "prev_exercise": prev_exercise,
        "next_exercise": next_exercise,
        "type_badge": EXERCISE_TYPE_BADGES.get(exercise.exercise_type, ""),
    }
    return render(request, "exercises/student/buchi_construct.html", context)


def _buchi_word_canvas(request, exercise):
    """Student page for buchi_word — read the fixed automaton, type an accepting word."""
    exercise_number, prev_exercise, next_exercise = _exercise_nav(exercise.id)
    last = (
        Attempt.objects.filter(exercise=exercise, student=request.profile)
        .order_by("-created_at")
        .first()
    )
    context = {
        "exercise": exercise,
        "exercise_number": exercise_number,
        "declared_aps": list(exercise.declared_aps or []),
        "elements_json": _elements_json(exercise.kripke_structure),
        "last_word": (last.answer or {}).get("word", "") if last else "",
        "word_symbols": [
            {"op": ",", "token": ", ", "label": "Separator"},
            {"op": "ω", "label": "Omega"},
        ],
        "is_completed": Attempt.objects.filter(
            exercise=exercise, student=request.profile, is_correct=True
        ).exists(),
        "prev_exercise": prev_exercise,
        "next_exercise": next_exercise,
        "type_badge": EXERCISE_TYPE_BADGES.get(exercise.exercise_type, ""),
    }
    return render(request, "exercises/student/buchi_word.html", context)
