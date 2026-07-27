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

from apps.accounts.middleware import teacher_page, teacher_required
from apps.checker.tasks import (
    run_buchi_target_check,
    run_ltl_check,
    run_model_solvable_check,
)

from ..constants import (
    BUILDER_OPERATORS,
    DIFFICULTIES,
    EXERCISE_TYPE_BADGES,
    FORMULA_INPUT_TYPES,
)
from ..models import Exercise, Topic
from ..services import (
    BUILDER_EXERCISE_TYPES,
    _elements_json,
    exercise_rows,
    formula_satisfiable,
    judge_answer_key,
    parse_exercise_form,
    persist_exercise,
    type_locked,
    validate_exercise_form,
)

logger = logging.getLogger(__name__)


def _topic_or_none(pk):
    """Resolve a Topic by PK, tolerating empty/invalid UUID input from forms."""
    if not pk:
        return None
    try:
        return Topic.objects.filter(pk=pk).first()
    except (ValueError, ValidationError):
        return None


@teacher_page("exercises")
def teacher_exercises(request):
    type_filters = [
        {"key": t, "label": EXERCISE_TYPE_BADGES.get(t, t)}
        for t in BUILDER_EXERCISE_TYPES
    ]
    return render(request, "exercises/teacher/list.html", {
        "exercises": exercise_rows(),
        "type_filters": type_filters,
    })


@teacher_page()
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
        exercise_type = (
            exercise.exercise_type if type_locked(exercise) else form["exercise_type"]
        )
        # must match the field validate_exercise_form read, which also keys off
        # the effective type — a locked exercise ignores the posted type
        raw_graph = (
            form["automaton_data"]
            if exercise_type == "buchi_word"
            else form["graph_data"]
        )
        try:
            elements_json = _elements_json(json.loads(raw_graph) if raw_graph else None)
        except json.JSONDecodeError:
            elements_json = ""
        prefill = form
        declared_aps = form["declared_aps"]
        parts = form["parts"]
        target_formula = form["target_formula"]
        ask_determinism = form["ask_determinism"]
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
        }
        exercise_type = exercise.exercise_type
        declared_aps = list(exercise.declared_aps or [])
        parts = [
            {"id": str(p.id), "prompt": p.prompt, "formula": p.formula,
             "hints": list(p.hints or [])}
            for p in exercise.parts.all()
        ]
        # legacy model_check reuses target_formula as its memo answer — only the
        # Büchi target belongs in the builder field
        target_formula = (
            exercise.target_formula or ""
            if exercise.exercise_type == "buchi_construct"
            else ""
        )
        ask_determinism = exercise.ask_determinism
    else:
        hint_values = ["", "", ""]
        allowed = list(BUILDER_OPERATORS)
        elements_json = ""
        prefill = None
        exercise_type = "model_check"
        declared_aps = []
        parts = []
        target_formula = ""
        ask_determinism = False
    # the page carries a Kripke editor and a Büchi editor at once; each gets only
    # its own type's structure so neither boots with the other's shape
    is_automaton = exercise_type == "buchi_word"
    automaton_elements_json = elements_json if is_automaton else ""
    if is_automaton:
        elements_json = ""
    return {
        "modules": list(Topic.objects.all()),
        "operators": BUILDER_OPERATORS,
        "difficulties": DIFFICULTIES,
        "hint_values": hint_values,
        "allowed_operators": allowed,
        "elements_json": elements_json,
        "automaton_elements_json": automaton_elements_json,
        "prefill": prefill,
        # str: POST re-render carries the id as a string, the edit path as a UUID
        "selected_topic_id": str(prefill["module_id"]) if prefill and prefill["module_id"] else None,
        "is_edit": exercise is not None,
        "exercise_id": exercise.id if exercise else None,
        "exercise_type": exercise_type,
        "type_locked": type_locked(exercise),
        "builder_types": BUILDER_EXERCISE_TYPES,
        "declared_aps_json": json.dumps(declared_aps),
        "parts_json": json.dumps(parts),
        "target_formula": target_formula,
        "ask_determinism": ask_determinism,
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
            context["selected_topic_id"] = str(topic.id)
    return render(request, "exercises/teacher/builder.html", context)


def _save_exercise(request, exercise):
    publishing = request.POST.get("action", "draft") == "publish"
    form = parse_exercise_form(request)
    errors, graph = validate_exercise_form(form, exercise, publishing)
    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, "exercises/teacher/builder.html", _builder_context(exercise, form))

    saved = persist_exercise(exercise, form, graph, publishing)
    messages.success(request, "Exercise published." if publishing else "Draft saved.")
    reset = getattr(saved, "_attempts_reset", 0)
    if reset:
        messages.warning(
            request,
            f"Editing the graph or formulas reset {reset} student "
            f"submission{'s' if reset != 1 else ''} — students will resubmit.",
        )
    if not form["allowed_operators"] and saved.exercise_type in FORMULA_INPUT_TYPES:
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
def test_formula(request):
    """Builder Test button — check a part formula against the live editor graph.

    Always answers 200 JSON so the client has a single handler; ok=False
    carries the user-facing problem (parse error, caps, malformed payload).
    """
    try:
        payload = json.loads(request.body or b"")
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Malformed request."})
    graph = payload.get("graph")
    formula = str(payload.get("formula") or "").strip()
    mode = payload.get("mode")
    if mode not in ("satisfiable", "holds", "solvable", "buchi_target"):
        return JsonResponse({"ok": False, "error": "Unknown test mode."})
    if not formula:
        return JsonResponse({"ok": False, "error": "Enter a formula to test."})
    # buchi_construct has no memo graph — report the target automaton over Σ
    if mode == "buchi_target":
        symbols = payload.get("declared_aps")
        if not isinstance(symbols, list):
            symbols = []
        try:
            info = run_buchi_target_check(formula, symbols)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)})
        if info["empty"]:
            return JsonResponse({
                "ok": True,
                "result": "no word over the alphabet satisfies this — unsolvable",
            })
        return JsonResponse({
            "ok": True, "result": f"accepted by a {info['states']}-state Büchi automaton",
        })
    # build_kripke has no memo graph — test satisfiability instead
    if mode == "solvable":
        declared_aps = payload.get("declared_aps")
        if not isinstance(declared_aps, list):
            declared_aps = []
        try:
            solvable = run_model_solvable_check([formula], declared_aps)["solvable"]
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)})
        return JsonResponse({"ok": True, "result": "solvable" if solvable else "unsolvable"})
    if not isinstance(graph, dict) or not graph:
        return JsonResponse({"ok": False, "error": "Draw a Kripke structure first."})
    try:
        if mode == "satisfiable":
            result = "satisfiable" if formula_satisfiable(graph, formula) else "unsatisfiable"
        else:
            result = "holds" if run_ltl_check(graph, formula)["result"] == "satisfied" else "violated"
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
    except Exception:
        logger.exception("test_formula failed")
        return JsonResponse(
            {"ok": False, "error": "Verification was stopped — the formula or graph could not be processed."}
        )
    return JsonResponse({"ok": True, "result": result})


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
