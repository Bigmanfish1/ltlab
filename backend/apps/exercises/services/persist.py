import json

from django.db.models import Max
from django.utils import timezone

from apps.checker.tasks import run_ltl_check

from ..models import Attempt, Exercise, ExercisePart
from .common import _effective_type, _has_attempts


def _grading_signature(graph, allowed_operators, declared_aps, parts, target_formula=None, ask_determinism=False):
    """Stable fingerprint of everything that determines how answers are graded.

    parts is a list of (prompt, formula) in position order. Title, description,
    hints and difficulty are excluded — editing them never invalidates a
    student's answer, so they must not trigger a reset.
    """
    return json.dumps(
        {
            "graph": graph,
            "ops": sorted(allowed_operators or []),
            "aps": sorted(declared_aps or []),
            "parts": [[p, f] for p, f in parts],
            "target": target_formula,
            "ask_determinism": ask_determinism,
        },
        sort_keys=True,
        default=str,
    )


def _exercise_grading_signature(exercise):
    parts = [(p.prompt, p.formula) for p in exercise.parts.all()]
    return _grading_signature(
        exercise.kripke_structure, exercise.allowed_operators,
        exercise.declared_aps, parts,
        exercise.target_formula if exercise.exercise_type == "buchi_construct" else None,
        exercise.ask_determinism,
    )


def _store_judge_answers(exercise):
    """Cache each judge part's holds-verdict so grading need not re-check SPOT."""
    graph = exercise.kripke_structure
    for part in exercise.parts.all():
        holds = None
        if graph:
            try:
                holds = run_ltl_check(graph, part.formula)["result"] == "satisfied"
            except ValueError:
                holds = None
        if part.answer_holds != holds:
            part.answer_holds = holds
            part.save(update_fields=["answer_holds"])


def persist_exercise(exercise, form, graph, publishing):
    old_signature = None
    had_attempts = False
    if exercise is not None:
        old_signature = _exercise_grading_signature(exercise)
        had_attempts = _has_attempts(exercise)
        exercise.topic_id = form["module_id"]
        new_type = _effective_type(form, exercise)
        if new_type != exercise.exercise_type:
            # only reachable while never-published with zero attempts, so the
            # wipe destroys teacher-authored parts, never student data
            was_buchi_construct = exercise.exercise_type == "buchi_construct"
            exercise.exercise_type = new_type
            exercise.parts.all().delete()
            if was_buchi_construct:
                # the builder hides these fields on other types, so leaving them
                # set would strand a hidden answer key the teacher cannot see —
                # and model_check reads target_formula as its own memo answer
                exercise.target_formula = None
                exercise.ask_determinism = False
    else:
        next_position = (
            Exercise.objects.filter(topic_id=form["module_id"]).aggregate(
                m=Max("position")
            )["m"]
        )
        exercise = Exercise(
            topic_id=form["module_id"],
            created_at=timezone.now(),
            exercise_type=form["exercise_type"],
            position=(next_position + 1) if next_position is not None else 0,
        )
    exercise.title = form["title"]
    exercise.description = form["description"]
    exercise.difficulty = form["difficulty"]
    # global hints belong to the partless types; part types carry hints per part
    global_hints = (
        form["hints"]
        if exercise.exercise_type in ("model_check", "buchi_construct", "buchi_word")
        else []
    )
    exercise.hints = global_hints
    exercise.hint = next((h for h in global_hints if h), "")
    exercise.allowed_operators = form["allowed_operators"]
    exercise.declared_aps = form["declared_aps"]
    # build_kripke / buchi_construct are student-built — never persist the
    # builder's hidden editor as a memorandum
    exercise.kripke_structure = (
        None
        if exercise.exercise_type in ("build_kripke", "buchi_construct")
        else graph
    )
    if exercise.exercise_type == "buchi_construct":
        exercise.target_formula = form["target_formula"]
        exercise.ask_determinism = form["ask_determinism"]
    exercise.is_published = publishing
    if publishing:
        exercise.ever_published = True
    exercise.save()
    if exercise.exercise_type not in ("model_check", "buchi_construct", "buchi_word"):
        _sync_parts(exercise, form["parts"])
    if exercise.exercise_type == "judge":
        _store_judge_answers(exercise)

    # Editing what an answer is graded against invalidates existing answers;
    # rather than silently keep stale grades (or unfairly re-grade), clear the
    # attempts so students resubmit against the new definition.
    exercise._attempts_reset = 0
    if old_signature is not None and had_attempts:
        if _exercise_grading_signature(exercise) != old_signature:
            deleted, _ = Attempt.objects.filter(exercise=exercise).delete()
            exercise._attempts_reset = deleted
    return exercise


def _sync_parts(exercise, parts):
    """Diff-sync parts by id: update kept rows, create new, delete removed.

    Never delete-and-recreate — Attempt.part is CASCADE, so a blanket recreate
    would silently destroy student attempts on unchanged parts.
    """
    existing = {str(p.id): p for p in exercise.parts.all()}
    kept_ids = set()
    for position, data in enumerate(parts):
        part = existing.get(data["id"])
        if part is not None:
            kept_ids.add(data["id"])
            if (part.prompt, part.formula, part.hints, part.position) != (
                data["prompt"], data["formula"], data["hints"], position,
            ):
                part.prompt = data["prompt"]
                part.formula = data["formula"]
                part.hints = data["hints"]
                part.position = position
                part.save(update_fields=["prompt", "formula", "hints", "position"])
        else:
            part = ExercisePart.objects.create(
                exercise=exercise,
                position=position,
                prompt=data["prompt"],
                formula=data["formula"],
                hints=data["hints"],
            )
            kept_ids.add(str(part.id))
    exercise.parts.exclude(id__in=kept_ids).delete()
