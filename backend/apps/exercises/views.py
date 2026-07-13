import json
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.middleware import supabase_login_required, teacher_required
from apps.checker.operators import disallowed_operators
from apps.checker.tasks import run_ltl_check
from apps.checker.views import MAX_FORMULA_CHARS, build_result_context, error_response

from .constants import BUILDER_OPERATORS, DIFFICULTIES, OPERATOR_LABELS
from .models import Attempt, Exercise, Topic
from .services import (
    BUILDER_EXERCISE_TYPES,
    _elements_json,
    exercise_rows,
    parse_exercise_form,
    persist_exercise,
    validate_exercise_form,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Student-facing views (DB-backed)
# ---------------------------------------------------------------------------

def published_exercises():
    """Exercises visible to students — drafts (is_published=False) are excluded."""
    return Exercise.objects.filter(is_published=True)


@supabase_login_required
def exercises(request):
    exercises_data = []
    for exercise in published_exercises():
        attempt_count = Attempt.objects.filter(exercise=exercise, student=request.profile).count()
        is_completed = Attempt.objects.filter(
            exercise=exercise, student=request.profile, is_correct=True
        ).exists()
        exercises_data.append({
            'exercise': exercise,
            'is_completed': is_completed,
            'attempt_count': attempt_count,
            'best_attempt': None,
        })

    return render(request, 'exercises/exercises.html', {'exercises_data': exercises_data})


@supabase_login_required
def exercise_canvas(request, exercise_id):
    """Exercise canvas with Kripke model, formula input, and submission"""
    exercise = get_object_or_404(published_exercises(), id=exercise_id)

    attempts = Attempt.objects.filter(exercise=exercise, student=request.profile)
    is_completed = Attempt.objects.filter(
        exercise=exercise, student=request.profile, is_correct=True
    ).exists()

    all_exercises = list(published_exercises().order_by('position', 'id'))
    current_index = next((i for i, ex in enumerate(all_exercises) if ex.id == exercise_id), 0)
    prev_exercise = all_exercises[current_index - 1] if current_index > 0 else None
    next_exercise = all_exercises[current_index + 1] if current_index < len(all_exercises) - 1 else None

    # only the operators the teacher allowed (None = legacy exercise = all)
    allowed = exercise.allowed_operators if exercise.allowed_operators is not None else BUILDER_OPERATORS
    operator_buttons = [
        {"op": op, "label": OPERATOR_LABELS.get(op, op)}
        for op in BUILDER_OPERATORS if op in allowed
    ]

    context = {
        'exercise': exercise,
        'exercise_number': current_index + 1,
        'elements_json': _elements_json(exercise.kripke_structure),
        'operator_buttons': operator_buttons,
        'attempts': attempts,
        'is_completed': is_completed,
        'prev_exercise': prev_exercise,
        'next_exercise': next_exercise,
    }
    return render(request, 'exercises/exercise_canvas.html', context)


@supabase_login_required
@require_POST
def submit_formula(request, exercise_id):
    """Grade a submission by model-checking it against the exercise's graph.

    Same engine path as the sandbox: run_ltl_check(graph, formula) → satisfied
    means correct. Renders the shared sandbox/result.html fragment (real
    counterexample trace included) rather than a fabricated one.
    """
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    student = request.profile

    formula = request.POST.get('formula', '').strip()
    if not formula:
        return error_response(request, "Enter a formula to check.")
    if len(formula) > MAX_FORMULA_CHARS:
        return error_response(
            request, f"Formula is too long — at most {MAX_FORMULA_CHARS} characters."
        )

    graph = exercise.kripke_structure
    if not graph:
        return error_response(request, "This exercise has no model to check against.")

    if exercise.allowed_operators is not None:
        bad = disallowed_operators(formula, exercise.allowed_operators)
        if bad:
            labels = sorted(
                f"{t} ({OPERATOR_LABELS[t]})" if t in OPERATOR_LABELS else t for t in bad
            )
            return error_response(
                request,
                "These operators aren't allowed for this exercise: " + ", ".join(labels) + ".",
            )

    try:
        result = run_ltl_check(graph, formula)
    except ValueError as exc:
        return error_response(request, str(exc))
    except Exception:
        logger.exception("run_ltl_check failed during exercise submission")
        return error_response(
            request, "Verification was stopped — the formula or graph could not be processed."
        )

    is_correct = result["result"] == "satisfied"

    hint_count = len([h for h in (exercise.hints or []) if h and h.strip()])
    try:
        hints_used = min(max(0, int(request.POST.get('hints_used', 0))), hint_count)
    except (TypeError, ValueError):
        hints_used = 0

    Attempt.objects.create(
        exercise=exercise,
        student=student,
        formula_input=formula,
        is_correct=is_correct,
        hints_used=hints_used,
    )

    context = build_result_context(result, json.dumps(result["kripke_graph"]))
    response = render(request, "sandbox/result.html", context)
    if is_correct:
        response["HX-Trigger"] = "exerciseSolved"
    return response


# ---------------------------------------------------------------------------
# Teacher-facing views (authoring)
# ---------------------------------------------------------------------------

def _topic_or_none(pk):
    """Resolve a Topic by PK, tolerating empty/invalid UUID input from forms."""
    if not pk:
        return None
    try:
        return Topic.objects.filter(pk=pk).first()
    except (ValueError, ValidationError):
        return None


@teacher_required
def teacher_exercises(request):
    return render(request, "exercises/teacher_exercises.html", {
        "exercises": exercise_rows(),
    })


@teacher_required
def manage(request):
    topics = list(Topic.objects.select_related("unlocks_after").prefetch_related("exercises"))
    modules = []
    for i, t in enumerate(topics, start=1):
        modules.append({
            "id": t.id,
            "index": f"{i:02d}",
            "title": t.title,
            "description": t.description or "",
            "unlocks_after": t.unlocks_after.title if t.unlocks_after_id else "None",
            "unlocks_after_id": t.unlocks_after_id or "",
            "visible": t.visible,
            "exercises": [
                {"id": e.id, "name": e.title, "difficulty": e.difficulty, "is_published": e.is_published}
                for e in t.exercises.all()
            ],
        })
    return render(request, "manage/teacher_manage.html", {
        "modules": modules,
        "topics": topics,
    })


def _builder_context(exercise, form=None):
    if form is not None:
        hint_values = form["hints"]
        allowed = form["allowed_operators"]
        try:
            elements_json = _elements_json(json.loads(form["graph_data"]) if form["graph_data"] else None)
        except json.JSONDecodeError:
            elements_json = ""
        prefill = form
        exercise_type = exercise.exercise_type if exercise else form["exercise_type"]
        declared_aps = form["declared_aps"]
        parts = form["parts"]
    elif exercise is not None:
        hints = list(exercise.hints or [])[:3]
        hint_values = hints + [""] * (3 - len(hints))
        allowed = (
            exercise.allowed_operators
            if exercise.allowed_operators is not None
            else BUILDER_OPERATORS
        )
        elements_json = _elements_json(exercise.kripke_structure)
        prefill = {
            "title": exercise.title,
            "description": exercise.description,
            "difficulty": exercise.difficulty,
            "module_id": exercise.topic_id,
            "target_formula": exercise.target_formula,
        }
        exercise_type = exercise.exercise_type
        declared_aps = list(exercise.declared_aps or [])
        parts = [
            {"id": str(p.id), "prompt": p.prompt, "formula": p.formula}
            for p in exercise.parts.all()
        ]
    else:
        hint_values = ["", "", ""]
        allowed = list(BUILDER_OPERATORS)
        elements_json = ""
        prefill = None
        exercise_type = "model_check"
        declared_aps = []
        parts = []
    return {
        "modules": list(Topic.objects.all()),
        "operators": BUILDER_OPERATORS,
        "difficulties": DIFFICULTIES,
        "hint_values": hint_values,
        "allowed_operators": allowed,
        "elements_json": elements_json,
        "prefill": prefill,
        "selected_topic_id": prefill["module_id"] if prefill else None,
        "is_edit": exercise is not None,
        "exercise_id": exercise.id if exercise else None,
        "exercise_type": exercise_type,
        "builder_types": BUILDER_EXERCISE_TYPES,
        "declared_aps_json": json.dumps(declared_aps),
        "parts_json": json.dumps(parts),
    }


@teacher_required
def exercise_builder(request, exercise_id=None):
    exercise = get_object_or_404(Exercise, pk=exercise_id) if exercise_id else None

    if request.method == "POST":
        return _save_exercise(request, exercise)

    context = _builder_context(exercise)
    if exercise is None:
        topic = _topic_or_none(request.GET.get("topic", ""))
        if topic is not None:
            context["selected_topic_id"] = topic.id
    return render(request, "exercises/teacher_exercise_builder.html", context)


def _save_exercise(request, exercise):
    publishing = request.POST.get("action", "draft") == "publish"
    form = parse_exercise_form(request)
    errors, graph = validate_exercise_form(form, exercise, publishing)
    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, "exercises/teacher_exercise_builder.html", _builder_context(exercise, form))

    persist_exercise(exercise, form, graph, publishing)
    messages.success(request, "Exercise published." if publishing else "Draft saved.")
    if not form["allowed_operators"]:
        messages.warning(request, "No operators are enabled — students can only submit atomic propositions.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_create(request):
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Module title is required.")
        return redirect("manage")
    unlocks = _topic_or_none(request.POST.get("unlocks_after", "").strip())
    highest = Topic.objects.aggregate(m=Max("position"))["m"]
    position = (highest if highest is not None else -1) + 1
    try:
        with transaction.atomic():
            Topic.objects.create(
                title=title,
                description=request.POST.get("description", "").strip(),
                visible=request.POST.get("visible") == "1",
                unlocks_after=unlocks,
                position=position,
                created_by=request.profile,
            )
    except IntegrityError:
        messages.error(request, "A module with that name already exists.")
        return redirect("manage")
    messages.success(request, "Module created.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_update(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Module title is required.")
        return redirect("manage")
    unlocks = _topic_or_none(request.POST.get("unlocks_after", "").strip())
    if unlocks and unlocks.id == topic.id:
        unlocks = None
    topic.title = title
    topic.description = request.POST.get("description", "").strip()
    topic.visible = request.POST.get("visible") == "1"
    topic.unlocks_after = unlocks
    try:
        with transaction.atomic():
            topic.save()
    except IntegrityError:
        messages.error(request, "A module with that name already exists.")
        return redirect("manage")
    messages.success(request, "Module updated.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_delete(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    topic.delete()
    messages.success(request, "Module deleted.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_visibility(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    topic.visible = not topic.visible
    topic.save(update_fields=["visible"])
    return JsonResponse({"visible": topic.visible})


@teacher_required
@require_POST
def exercise_delete(request, exercise_id):
    exercise = get_object_or_404(Exercise, pk=exercise_id)
    exercise.delete()
    messages.success(request, "Exercise deleted.")
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("manage")


@teacher_required
@require_POST
def topic_reorder(request):
    try:
        order = json.loads(request.POST.get("order") or "[]")
    except json.JSONDecodeError:
        order = []
    for pos, tid in enumerate(order):
        try:
            Topic.objects.filter(pk=tid).update(position=pos)
        except (ValueError, ValidationError):
            continue
    return JsonResponse({"ok": True})
