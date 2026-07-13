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
from apps.checker.tasks import run_equivalence_check, run_ltl_check, run_trace_check
from apps.checker.views import MAX_FORMULA_CHARS, build_result_context, error_response

from .constants import (
    BUILDER_OPERATORS,
    DIFFICULTIES,
    EXERCISE_TYPE_BADGES,
    OPERATOR_LABELS,
)
from .models import Attempt, Exercise, ExercisePart, Topic
from .services import (
    BUILDER_EXERCISE_TYPES,
    _elements_json,
    exercise_rows,
    judge_answer_key,
    parse_exercise_form,
    persist_exercise,
    solved_exercise_ids,
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

    return render(request, 'exercises/exercises.html', {'exercises_data': exercises_data})


def _exercise_nav(exercise_id):
    all_exercises = list(published_exercises().order_by('position', 'id'))
    current_index = next((i for i, ex in enumerate(all_exercises) if ex.id == exercise_id), 0)
    prev_exercise = all_exercises[current_index - 1] if current_index > 0 else None
    next_exercise = all_exercises[current_index + 1] if current_index < len(all_exercises) - 1 else None
    return current_index + 1, prev_exercise, next_exercise


def _operator_buttons(exercise):
    # only the operators the teacher allowed (None = legacy exercise = all)
    allowed = exercise.allowed_operators if exercise.allowed_operators is not None else BUILDER_OPERATORS
    return [
        {"op": op, "label": OPERATOR_LABELS.get(op, op)}
        for op in BUILDER_OPERATORS if op in allowed
    ]


@supabase_login_required
def exercise_canvas(request, exercise_id):
    """Exercise page — dispatches to the type-specific template."""
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    if exercise.exercise_type == "english_to_formula":
        return _english_canvas(request, exercise)
    if exercise.exercise_type == "path_exhibit":
        return _path_canvas(request, exercise)
    if exercise.exercise_type == "judge":
        return _judge_canvas(request, exercise)

    attempts = Attempt.objects.filter(exercise=exercise, student=request.profile)
    is_completed = Attempt.objects.filter(
        exercise=exercise, student=request.profile, is_correct=True
    ).exists()

    exercise_number, prev_exercise, next_exercise = _exercise_nav(exercise_id)

    context = {
        'exercise': exercise,
        'exercise_number': exercise_number,
        'elements_json': _elements_json(exercise.kripke_structure),
        'operator_buttons': _operator_buttons(exercise),
        'attempts': attempts,
        'is_completed': is_completed,
        'prev_exercise': prev_exercise,
        'next_exercise': next_exercise,
    }
    return render(request, 'exercises/exercise_canvas.html', context)


def _part_rows(exercise, student):
    parts = list(exercise.parts.all())
    correct_part_ids = set(
        Attempt.objects.filter(
            exercise=exercise, student=student,
            is_correct=True, part__isnull=False,
        ).values_list("part_id", flat=True)
    )
    return [
        {"part": p, "number": i, "solved": p.id in correct_part_ids}
        for i, p in enumerate(parts, start=1)
    ]


def _path_canvas(request, exercise):
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
    }
    return render(request, "exercises/exercise_path.html", context)


def _judge_canvas(request, exercise):
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
    }
    return render(request, "exercises/exercise_judge.html", context)


def _english_canvas(request, exercise):
    part_rows = _part_rows(exercise, request.profile)
    exercise_number, prev_exercise, next_exercise = _exercise_nav(exercise.id)
    context = {
        "exercise": exercise,
        "exercise_number": exercise_number,
        "part_rows": part_rows,
        "declared_aps": list(exercise.declared_aps or []),
        "operator_buttons": _operator_buttons(exercise),
        "is_completed": bool(part_rows) and all(r["solved"] for r in part_rows),
        "prev_exercise": prev_exercise,
        "next_exercise": next_exercise,
    }
    return render(request, "exercises/exercise_english.html", context)


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


def _part_result(request, part, status, message):
    return render(request, "exercises/part_result.html", {
        "part": part,
        "status": status,
        "message": message,
    })


def _clamped_hints(request, exercise):
    hint_count = len([h for h in (exercise.hints or []) if h and h.strip()])
    try:
        return min(max(0, int(request.POST.get("hints_used", 0))), hint_count)
    except (TypeError, ValueError):
        return 0


def _completion_trigger(response, request, exercise):
    if exercise.id in solved_exercise_ids(
        request.profile, Exercise.objects.filter(pk=exercise.pk)
    ):
        response["HX-Trigger"] = "exerciseSolved"
    return response


def _parse_trace_field(request, name):
    try:
        value = json.loads(request.POST.get(name) or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        return None
    return value


def _submit_path_part(request, exercise, part):
    prefix = _parse_trace_field(request, "trace_prefix")
    cycle = _parse_trace_field(request, "trace_cycle")
    if prefix is None or cycle is None:
        return _part_result(request, part, "error", "Select a path before submitting.")
    if not cycle:
        return _part_result(
            request, part, "error",
            "Close the loop first — click a state already on your path to form the cycle.",
        )
    if not exercise.kripke_structure:
        return _part_result(request, part, "error", "This exercise has no model to check against.")

    try:
        result = run_trace_check(exercise.kripke_structure, part.formula, prefix, cycle)
    except ValueError as exc:
        return _part_result(request, part, "error", str(exc))
    except Exception:
        logger.exception("run_trace_check failed during part submission")
        return _part_result(
            request, part, "error",
            "Verification was stopped — the path could not be processed.",
        )

    is_correct = bool(result["path_ok"] and result["holds"])
    Attempt.objects.create(
        exercise=exercise,
        student=request.profile,
        part=part,
        answer={"prefix": prefix, "cycle": cycle},
        is_correct=is_correct,
        hints_used=_clamped_hints(request, exercise),
        misconception="",
    )

    if is_correct:
        response = _part_result(
            request, part, "correct",
            "Correct — your path satisfies the formula.",
        )
        return _completion_trigger(response, request, exercise)
    if not result["path_ok"]:
        return _part_result(request, part, "incorrect", result["path_error"])
    return _part_result(
        request, part, "incorrect",
        "That is a valid path of the model, but the formula does not hold on it.",
    )


def _submit_judge_part(request, exercise, part):
    verdict = request.POST.get("verdict", "").strip()
    if verdict not in ("holds", "violated"):
        return _part_result(request, part, "error", "Choose a verdict first.")
    if not exercise.kripke_structure:
        return _part_result(request, part, "error", "This exercise has no model to check against.")

    try:
        truth = run_ltl_check(exercise.kripke_structure, part.formula)["result"]
    except ValueError as exc:
        return _part_result(request, part, "error", str(exc))
    except Exception:
        logger.exception("run_ltl_check failed during judge submission")
        return _part_result(
            request, part, "error",
            "Verification was stopped — the formula could not be processed.",
        )
    actually_holds = truth == "satisfied"

    if verdict == "holds":
        answer = {"verdict": "holds"}
        is_correct = actually_holds
        message = (
            "Correct — the formula holds on every path of the model."
            if is_correct
            else "The formula does not hold universally — there is a path that violates it."
        )
    else:
        prefix = _parse_trace_field(request, "trace_prefix")
        cycle = _parse_trace_field(request, "trace_cycle")
        if prefix is None or cycle is None:
            return _part_result(
                request, part, "error",
                "Select a counterexample path before submitting.",
            )
        if not cycle:
            return _part_result(
                request, part, "error",
                "Close the loop first — click a state already on your path to form the cycle.",
            )
        try:
            trace = run_trace_check(exercise.kripke_structure, part.formula, prefix, cycle)
        except ValueError as exc:
            return _part_result(request, part, "error", str(exc))
        answer = {"verdict": "violated", "prefix": prefix, "cycle": cycle}
        is_correct = (
            not actually_holds and trace["path_ok"] and trace["holds"] is False
        )
        if is_correct:
            message = "Correct — the formula does not hold, and your path witnesses the violation."
        elif actually_holds:
            message = "The formula actually holds on every path — no counterexample exists."
        elif not trace["path_ok"]:
            message = trace["path_error"]
        else:
            message = "The formula holds on your chosen path — find a path where it fails."

    Attempt.objects.create(
        exercise=exercise,
        student=request.profile,
        part=part,
        answer=answer,
        is_correct=is_correct,
        hints_used=_clamped_hints(request, exercise),
        misconception="",
    )

    response = _part_result(
        request, part, "correct" if is_correct else "incorrect", message
    )
    if is_correct:
        return _completion_trigger(response, request, exercise)
    return response


@supabase_login_required
@require_POST
def submit_part(request, exercise_id, part_id):
    """Grade a sub-question submission and render its result partial."""
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    part = get_object_or_404(ExercisePart, pk=part_id, exercise=exercise)

    if exercise.exercise_type == "path_exhibit":
        return _submit_path_part(request, exercise, part)
    if exercise.exercise_type == "judge":
        return _submit_judge_part(request, exercise, part)
    if exercise.exercise_type != "english_to_formula":
        return _part_result(request, part, "error", "This exercise type is not submittable yet.")

    formula = request.POST.get("formula", "").strip()
    if not formula:
        return _part_result(request, part, "error", "Enter a formula to check.")
    if len(formula) > MAX_FORMULA_CHARS:
        return _part_result(
            request, part, "error",
            f"Formula is too long — at most {MAX_FORMULA_CHARS} characters.",
        )

    if exercise.allowed_operators is not None:
        bad = disallowed_operators(formula, exercise.allowed_operators)
        if bad:
            labels = sorted(
                f"{t} ({OPERATOR_LABELS[t]})" if t in OPERATOR_LABELS else t for t in bad
            )
            return _part_result(
                request, part, "error",
                "These operators aren't allowed for this exercise: " + ", ".join(labels) + ".",
            )

    try:
        result = run_equivalence_check(part.formula, formula, exercise.declared_aps or [])
    except ValueError as exc:
        return _part_result(request, part, "error", str(exc))
    except Exception:
        logger.exception("run_equivalence_check failed during part submission")
        return _part_result(
            request, part, "error",
            "Verification was stopped — the formula could not be processed.",
        )

    is_correct = result["equivalent"]

    Attempt.objects.create(
        exercise=exercise,
        student=request.profile,
        part=part,
        formula_input=formula,
        is_correct=is_correct,
        hints_used=_clamped_hints(request, exercise),
    )

    if is_correct:
        response = _part_result(
            request, part, "correct",
            "Correct — your formula is equivalent to the requirement.",
        )
        return _completion_trigger(response, request, exercise)
    return _part_result(
        request, part, "incorrect",
        "Not equivalent to the requirement — check which behaviours your formula allows or forbids.",
    )


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
                {"id": e.id, "name": e.title, "difficulty": e.difficulty,
                 "is_published": e.is_published,
                 "type_label": EXERCISE_TYPE_BADGES.get(e.exercise_type, "")}
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

    saved = persist_exercise(exercise, form, graph, publishing)
    messages.success(request, "Exercise published." if publishing else "Draft saved.")
    if not form["allowed_operators"]:
        messages.warning(request, "No operators are enabled — students can only submit atomic propositions.")
    if publishing and saved.exercise_type == "judge":
        key = ", ".join(
            f"{i}. {formula} — {'holds' if holds else 'does not hold'}"
            for i, formula, holds in judge_answer_key(saved)
        )
        messages.warning(request, f"Answer key: {key}")
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
