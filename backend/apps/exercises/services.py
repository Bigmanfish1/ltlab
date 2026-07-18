import json
import math
from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count, Max
from django.utils import timezone

from apps.accounts.models import Profile
from apps.checker.engine import validate_request
from apps.checker.equivalence import validate_formula_submission
from apps.checker.operators import disallowed_operators
from apps.checker.tasks import run_ltl_check, run_model_solvable_check
from apps.checker.views import _PROP_NAME_RE, _RESERVED_PROP_NAMES

from .constants import (
    DIFFICULTIES,
    EXERCISE_TYPE_BADGES,
    MISCONCEPTION_LABELS,
    OPERATOR_DISPLAY,
    OPERATOR_LABELS,
)
from .models import Attempt, Exercise, ExercisePart, Topic

BUILDER_EXERCISE_TYPES = (
    "model_check", "english_to_formula", "path_exhibit", "judge", "build_kripke",
    "buchi_construct", "buchi_word",
)


def enrolled_students():
    return Profile.objects.filter(role=Profile.ROLE_STUDENT).order_by("name", "id")


def enrolled_ids():
    return enrolled_students().values_list("id", flat=True)


def _round_half_up(x):
    # Python's round() is half-to-even; we want half-up. x is non-negative here.
    return math.floor(x + 0.5)


def _pct(n, d):
    return _round_half_up(100 * n / d) if d else 0


def _attempt_matrix():
    # numerator population must match the enrolled denominator, else completion can exceed 100%
    matrix = defaultdict(lambda: defaultdict(list))
    rows = Attempt.objects.filter(student_id__in=enrolled_ids()).values_list(
        "exercise_id", "student_id", "is_correct", "created_at", "formula_input", "hints_used", "part_id"
    ).order_by("created_at")
    for ex_id, st_id, correct, created, formula, hints, part_id in rows:
        matrix[ex_id][st_id].append(
            {"correct": correct, "created": created, "formula": formula,
             "hints": hints, "part": part_id}
        )
    return matrix


def _part_counts():
    return dict(
        ExercisePart.objects.values("exercise_id")
        .annotate(n=Count("id"))
        .values_list("exercise_id", "n")
    )


def _solve_index(attempts, part_count):
    """1-based index of the attempt that solved the exercise, or None.

    Partless: the first correct attempt. With parts: the attempt that made the
    last remaining part correct."""
    if not part_count:
        idx = next((i for i, a in enumerate(attempts) if a["correct"]), None)
        return idx + 1 if idx is not None else None
    solved_parts = set()
    for i, a in enumerate(attempts):
        if a["correct"] and a["part"] is not None:
            solved_parts.add(a["part"])
            if len(solved_parts) >= part_count:
                return i + 1
    return None


def _exercise_metrics(exercise, per_student, enrolled_count, part_count=0):
    total_attempts = sum(len(a) for a in per_student.values())
    solvers = 0
    tries_to_solve = []
    for attempts in per_student.values():
        solve_at = _solve_index(attempts, part_count)
        if solve_at is not None:
            solvers += 1
            tries_to_solve.append(solve_at)
    engaged = len(per_student)
    completion_raw = 100 * solvers / enrolled_count if enrolled_count else 0.0
    completion = _pct(solvers, enrolled_count)
    # per-part normalisation keeps multi-part exercises comparable to
    # single-answer ones (a 6-part judge is not automatically "most struggled")
    per_part = max(part_count, 1)
    avg_tries = (
        round(sum(tries_to_solve) / len(tries_to_solve) / per_part, 1)
        if tries_to_solve else 0.0
    )
    fail_attempts = total_attempts - sum(
        1 for attempts in per_student.values() for a in attempts if a["correct"]
    )
    # struggle = mean submissions per engaged student; unsolved exercises rank high (unlike avg_tries)
    struggle = round(total_attempts / engaged / per_part, 1) if engaged else 0.0
    return {
        "attempts": total_attempts,
        "solvers": solvers,
        "completion": completion,
        "completion_raw": completion_raw,
        "avg_tries": avg_tries,
        "fail_attempts": fail_attempts,
        "struggle": struggle,
    }


def exercise_rows(matrix=None, part_counts=None):
    matrix = matrix if matrix is not None else _attempt_matrix()
    enrolled_count = enrolled_students().count()
    part_counts = part_counts if part_counts is not None else _part_counts()
    rows = []
    for ex in Exercise.objects.select_related("topic"):
        m = _exercise_metrics(
            ex, matrix.get(ex.id, {}), enrolled_count, part_counts.get(ex.id, 0)
        )
        rows.append({
            "id": ex.id,
            "name": ex.title,
            "module": ex.topic.title,
            "module_id": ex.topic_id,
            "difficulty": ex.difficulty,
            "exercise_type": ex.exercise_type,
            "type_label": EXERCISE_TYPE_BADGES.get(ex.exercise_type, ""),
            "is_published": ex.is_published,
            "attempts": m["attempts"],
            "completion": m["completion"],
            "completion_raw": m["completion_raw"],
            "avg_tries": m["avg_tries"],
            "struggle": m["struggle"],
        })
    return rows


def topic_completion(rows=None):
    rows = rows if rows is not None else exercise_rows()
    by_topic = defaultdict(list)
    for r in rows:
        # drafts are invisible to students — a guaranteed 0% would drag the average
        if not r["is_published"]:
            continue
        by_topic[r["module_id"]].append(r["completion_raw"])
    out = []
    for topic in Topic.objects.all():
        comps = by_topic.get(topic.id, [])
        # average the raw rates, round once (avoids compounding per-exercise rounding)
        out.append({"name": topic.title,
                    "completion": _round_half_up(sum(comps) / len(comps)) if comps else 0})
    return out


def class_metrics(matrix=None):
    matrix = matrix if matrix is not None else _attempt_matrix()
    enrolled_count = enrolled_students().count()
    total = correct = exercises_with_attempts = 0
    most_failed = None
    # only exercises with at least one wrong attempt qualify; 0.0 = no fails yet
    most_failed_rate = 0.0
    for ex in Exercise.objects.all():
        per_student = matrix.get(ex.id, {})
        ex_total = sum(len(a) for a in per_student.values())
        ex_correct = sum(1 for a in per_student.values() for x in a if x["correct"])
        total += ex_total
        correct += ex_correct
        if ex_total:
            exercises_with_attempts += 1
            fail_rate = (ex_total - ex_correct) / ex_total
            if fail_rate > most_failed_rate:
                most_failed_rate = fail_rate
                most_failed = ex.title
    return {
        "total_students": enrolled_count,
        "avg_accuracy": _pct(correct, total),
        "most_failed_exercise": most_failed or "N/A",
        "avg_attempts_per_ex": round(total / exercises_with_attempts, 1) if exercises_with_attempts else 0.0,
    }


def struggled_exercises(rows=None, limit=5):
    rows = rows if rows is not None else exercise_rows()
    rows = [r for r in rows if r["attempts"]]
    rows.sort(key=lambda r: r["struggle"], reverse=True)
    out = []
    for i, r in enumerate(rows[:limit], start=1):
        out.append({
            "rank": f"{i:02d}",
            "name": r["name"],
            "module": r["module"],
            "tries": r["struggle"],
            "solved": r["completion"],
        })
    return out


def solved_exercise_ids(student, exercises=None):
    """Ids of exercises the student has solved.

    Solved := every part has a correct attempt when parts exist, else any
    correct attempt — degenerates to the pre-parts behaviour for partless
    exercises, so legacy completion semantics are unchanged.
    """
    qs = exercises if exercises is not None else Exercise.objects.filter(is_published=True)
    ids = list(qs.values_list("id", flat=True))
    part_counts = dict(
        ExercisePart.objects.filter(exercise_id__in=ids)
        .values("exercise_id")
        .annotate(n=Count("id"))
        .values_list("exercise_id", "n")
    )
    # build_kripke stores requirements as parts but is graded as one whole
    # model, so completion is a single correct (partless) attempt, not per-part
    whole_exercise_ids = set(
        Exercise.objects.filter(id__in=ids, exercise_type="build_kripke")
        .values_list("id", flat=True)
    )
    correct = (
        Attempt.objects.filter(student=student, exercise_id__in=ids, is_correct=True)
        .values_list("exercise_id", "part_id")
        .distinct()
    )
    whole = set()
    parts_solved = defaultdict(set)
    for ex_id, part_id in correct:
        if part_id is None:
            whole.add(ex_id)
        else:
            parts_solved[ex_id].add(part_id)
    solved = set()
    for ex_id in ids:
        n = part_counts.get(ex_id, 0)
        if n and ex_id not in whole_exercise_ids:
            if len(parts_solved.get(ex_id, ())) >= n:
                solved.add(ex_id)
        elif ex_id in whole:
            solved.add(ex_id)
    return solved


def misconception_breakdown():
    # Placeholder pending a rework of LTL-error analytics for the new exercise
    # types; static sample so the Results panel still renders.
    sample = [("g_vs_f", 40), ("f_vs_x", 35), ("missing_global", 25)]
    return [
        {
            "key": key,
            "label": MISCONCEPTION_LABELS.get(key, key),
            "description": "sample data — analytics being reworked",
            "percentage": pct,
        }
        for key, pct in sample
    ]


def students_roster(matrix=None, part_counts=None):
    matrix = matrix if matrix is not None else _attempt_matrix()
    part_counts = part_counts if part_counts is not None else _part_counts()
    per_student = defaultdict(
        lambda: {"total": 0, "correct": 0, "whole": set(),
                 "parts": defaultdict(set), "last": None}
    )
    for ex_id, students in matrix.items():
        for st_id, attempts in students.items():
            acc = per_student[st_id]
            acc["total"] += len(attempts)
            for a in attempts:
                if a["correct"]:
                    acc["correct"] += 1
                    if a["part"] is None:
                        acc["whole"].add(ex_id)
                    else:
                        acc["parts"][ex_id].add(a["part"])
                if acc["last"] is None or a["created"] > acc["last"]:
                    acc["last"] = a["created"]
    out = []
    for student in enrolled_students():
        acc = per_student.get(student.id)
        if acc:
            solved = {
                ex_id for ex_id in acc["whole"] if not part_counts.get(ex_id)
            } | {
                ex_id for ex_id, parts in acc["parts"].items()
                if part_counts.get(ex_id) and len(parts) >= part_counts[ex_id]
            }
            out.append({
                "id": student.id,
                "name": student.name or student.email,
                "email": student.email,
                "exercises_done": len(solved),
                "accuracy": _pct(acc["correct"], acc["total"]),
                "last_active": _humanize(acc["last"]),
            })
        else:
            out.append({
                "id": student.id,
                "name": student.name or student.email,
                "email": student.email,
                "exercises_done": 0,
                "accuracy": 0,
                "last_active": "never",
            })
    out.sort(key=lambda s: s["exercises_done"], reverse=True)
    return out


def results_data():
    # Build the attempt matrix and exercise rows once, then feed every panel of
    # the Results page from them (was 4 matrix scans + 2 exercise_rows per load).
    matrix = _attempt_matrix()
    part_counts = _part_counts()
    rows = exercise_rows(matrix, part_counts)
    return {
        "metrics": class_metrics(matrix),
        "module_completion": topic_completion(rows),
        "struggled_exercises": struggled_exercises(rows),
        "misconceptions": misconception_breakdown(),
        "students": students_roster(matrix, part_counts),
    }


def dashboard_stats():
    matrix = _attempt_matrix()
    roster = students_roster(matrix)
    metrics = class_metrics(matrix)
    week_ago = timezone.now() - timedelta(days=7)
    active = (
        Attempt.objects.filter(created_at__gte=week_ago, student_id__in=enrolled_ids())
        .values("student_id").distinct().count()
    )
    return {
        "students_enrolled": metrics["total_students"],
        "active_this_week": active,
        "class_accuracy": metrics["avg_accuracy"],
        "roster": roster,
    }


def recent_activity(limit=6):
    out = []
    rows = (
        Attempt.objects.filter(student_id__in=enrolled_ids())
        .select_related("student", "exercise", "exercise__topic")
        .order_by("-created_at", "-id")[:limit]
    )
    for a in rows:
        name = a.student.name or a.student.email
        initials = "".join(p[0] for p in name.split()[:2]).upper() or "?"
        if a.is_correct and a.part_id is not None:
            verb = "solved a part of"
        elif a.is_correct:
            verb = "completed"
        else:
            verb = "attempted"
        out.append({
            "student_id": a.student_id,
            "initials": initials,
            "text": f"{name} {verb} {a.exercise.title} · {a.exercise.topic.title}",
            "time": _humanize(a.created_at),
            "type": "done" if a.is_correct else "stuck",
        })
    return out


def _fmt_lasso(answer):
    prefix = answer.get("prefix") or []
    cycle = answer.get("cycle") or []
    cycle_str = "(" + " → ".join(cycle) + ")ω"
    return " → ".join(prefix) + " → " + cycle_str if prefix else cycle_str


def _attempt_display(attempt):
    """One-line rendering of what the student submitted, whatever the type."""
    if attempt.formula_input:
        return attempt.formula_input
    answer = attempt.answer or {}
    if answer.get("verdict") == "holds":
        return "judged: holds"
    if answer.get("verdict") == "violated":
        return "judged: does not hold · " + _fmt_lasso(answer)
    if answer.get("cycle"):
        return "path: " + _fmt_lasso(answer)
    return ""


def student_detail(student):
    attempts = list(
        Attempt.objects.filter(student=student)
        .select_related("exercise", "part")
        .order_by("-created_at")
    )
    total = len(attempts)
    correct = sum(1 for a in attempts if a.is_correct)
    part_counts = _part_counts()
    whole = {a.exercise_id for a in attempts if a.is_correct and a.part_id is None}
    parts_solved = defaultdict(set)
    for a in attempts:
        if a.is_correct and a.part_id is not None:
            parts_solved[a.exercise_id].add(a.part_id)
    solved = {ex_id for ex_id in whole if not part_counts.get(ex_id)} | {
        ex_id for ex_id, parts in parts_solved.items()
        if part_counts.get(ex_id) and len(parts) >= part_counts[ex_id]
    }
    hints_used = sum(a.hints_used for a in attempts)
    history = [{
        "exercise": (
            f"{a.exercise.title} · Part {a.part.position + 1}" if a.part_id else a.exercise.title
        ),
        "formula": _attempt_display(a),
        "result": a.is_correct,
        "hints": a.hints_used,
        "date": _short_date(a.created_at),
    } for a in attempts[:12]]
    last = attempts[0] if attempts else None
    last_submission = None
    if last is not None:
        last_submission = {
            "formula": _attempt_display(last),
            "verdict": "Correct." if last.is_correct else "Incorrect.",
            "holds": last.is_correct,
            "elements_json": _elements_json(last.exercise.kripke_structure),
        }
    return {
        "id": student.id,
        "name": student.name or student.email,
        "exercises_done": len(solved),
        "accuracy": _pct(correct, total),
        "hints_used": hints_used,
        "history": history,
        "last_submission": last_submission,
    }


def _humanize(dt):
    if dt is None:
        return "never"
    delta = timezone.now() - dt
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    days = int(secs // 86400)
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _short_date(dt):
    # dt is UTC-aware from the DB; localize to the configured tz before formatting
    return timezone.localtime(dt).strftime("%d %b") if dt else ""


def _elements_json(structure):
    """Flatten a stored Kripke structure's nodes+edges into a Cytoscape JSON array."""
    if not structure or not isinstance(structure, dict):
        return ""
    elements = structure.get("elements") or {}
    array = (elements.get("nodes") or []) + (elements.get("edges") or [])
    return json.dumps(array) if array else ""


def _topic_exists(pk):
    """Existence check that tolerates empty/malformed UUID input from forms."""
    if not pk:
        return False
    try:
        return Topic.objects.filter(pk=pk).exists()
    except (ValueError, ValidationError):
        return False


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
    }


def _validate_declared_aps(declared_aps, errors):
    if not declared_aps:
        errors.append("Declare at least one atomic proposition.")
    for ap in declared_aps:
        if not _PROP_NAME_RE.match(ap):
            errors.append(f"'{ap}' is not a valid proposition name.")
        elif ap in _RESERVED_PROP_NAMES:
            errors.append(f"'{ap}' is a reserved LTL keyword.")


def _validate_english_parts(form, errors):
    if not form["parts"]:
        errors.append("Add at least one requirement with a target formula.")
    for i, part in enumerate(form["parts"], start=1):
        if not part["prompt"]:
            errors.append(f"Requirement {i} needs its English prompt.")
        if not part["formula"]:
            errors.append(f"Requirement {i} needs a target formula.")
            continue
        try:
            validate_formula_submission(part["formula"], form["declared_aps"])
        except ValueError as exc:
            errors.append(f"Requirement {i} target: {exc}")
            continue
        # a target using an operator students can't enter is unsolvable —
        # no permitted formula could ever be equivalent to it
        bad = disallowed_operators(part["formula"], form["allowed_operators"])
        if bad:
            labels = ", ".join(sorted(_operator_label(t) for t in bad))
            errors.append(
                f"Requirement {i} target uses operators students can't enter: {labels}. "
                "Enable them under Allowed Operators or rewrite the target."
            )


def _operator_label(token):
    shown = OPERATOR_DISPLAY.get(token, token)
    return f"{shown} ({OPERATOR_LABELS[token]})" if token in OPERATOR_LABELS else shown


def _validate_judge_parts(form, graph, errors):
    if not form["parts"]:
        errors.append("Add at least one formula for students to judge.")
        return
    for i, part in enumerate(form["parts"], start=1):
        if not part["formula"]:
            errors.append(f"Formula {i} is empty.")
            continue
        try:
            validate_request(graph, part["formula"])
        except ValueError as exc:
            errors.append(f"Formula {i}: {exc}")


def judge_answer_key(exercise):
    """(position, formula, holds) per part — the teacher-facing answer key.

    Reads the answer cached at save (_store_judge_answers), which is recomputed
    on every edit, so it never drifts from the graph."""
    return [
        (i, part.formula, bool(part.answer_holds))
        for i, part in enumerate(exercise.parts.all(), start=1)
    ]


def formula_satisfiable(graph, formula):
    """∃ path of the model satisfying φ — a counterexample to !(φ) is exactly
    a satisfying path for φ. Shared by the publish gate and the builder's
    Test button so the two can never disagree."""
    return run_ltl_check(graph, f"!({formula})")["result"] == "violated"


def _validate_path_parts(form, graph, errors):
    """Each formula must parse against the graph AND have a satisfying lasso —
    an unsatisfiable formula would make the part impossible for students."""
    if not form["parts"]:
        errors.append("Add at least one formula for students to find a path for.")
        return
    for i, part in enumerate(form["parts"], start=1):
        if not part["formula"]:
            errors.append(f"Formula {i} is empty.")
            continue
        try:
            satisfiable = formula_satisfiable(graph, part["formula"])
        except ValueError as exc:
            errors.append(f"Formula {i}: {exc}")
            continue
        if not satisfiable:
            errors.append(
                f"Formula {i} ({part['formula']}) has no satisfying path on this "
                "model — students could never solve it."
            )


def _validate_build_kripke_parts(form, errors):
    """Require ≥1 formula, all over the declared APs and jointly satisfiable."""
    if not form["parts"]:
        errors.append("Add at least one formula the student's model must satisfy.")
        return
    formulas = []
    for i, part in enumerate(form["parts"], start=1):
        if not part["formula"]:
            errors.append(f"Formula {i} is empty.")
            continue
        formulas.append(part["formula"])
    if len(formulas) != len(form["parts"]):
        return
    try:
        solvable = run_model_solvable_check(formulas, form["declared_aps"])["solvable"]
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not solvable:
        errors.append(
            "These requirements contradict each other — no Kripke structure can "
            "satisfy them all, so students could never solve the exercise."
        )


def _has_attempts(exercise):
    # memoised per instance — type_locked and persist both need it within one
    # request, and the exercise object is shared across them
    cached = getattr(exercise, "_has_attempts_cache", None)
    if cached is None:
        cached = exercise.attempts.exists()
        exercise._has_attempts_cache = cached
    return cached


def type_locked(exercise):
    """Type changes are only safe while no student could have seen the
    exercise: lock once ever published, or if any attempts exist (covers
    prod rows published-then-drafted before ever_published landed)."""
    return exercise is not None and (
        exercise.ever_published or _has_attempts(exercise)
    )


def _effective_type(form, exercise):
    return form["exercise_type"] if not type_locked(exercise) else exercise.exercise_type


def validate_exercise_form(form, exercise, publishing):
    errors = []
    if not form["title"]:
        errors.append("Exercise title is required.")
    if not form["description"]:
        errors.append("Task description is required.")
    if form["difficulty"] not in DIFFICULTIES:
        errors.append("Select a difficulty.")
    if not _topic_exists(form["module_id"]):
        errors.append("Assign the exercise to a module.")

    exercise_type = _effective_type(form, exercise)
    if exercise_type not in BUILDER_EXERCISE_TYPES:
        errors.append("Unknown exercise type.")
        return errors, None

    graph = None
    if form["graph_data"]:
        try:
            graph = json.loads(form["graph_data"])
        except json.JSONDecodeError:
            errors.append("The Kripke structure could not be read.")
    elif exercise is not None:
        graph = exercise.kripke_structure

    if publishing and not errors:
        if exercise_type == "english_to_formula":
            _validate_declared_aps(form["declared_aps"], errors)
            _validate_english_parts(form, errors)
        elif exercise_type == "build_kripke":
            # student supplies the graph; validate the required formulas instead
            _validate_declared_aps(form["declared_aps"], errors)
            _validate_build_kripke_parts(form, errors)
        elif not graph:
            # Students are graded against this graph (model-checking their
            # formula, or walking their path on it), so publishing needs one.
            errors.append("Publishing needs a memorandum Kripke structure.")
        elif exercise_type == "path_exhibit":
            _validate_path_parts(form, graph, errors)
        elif exercise_type == "judge":
            _validate_judge_parts(form, graph, errors)
    return errors, graph


def _grading_signature(graph, allowed_operators, declared_aps, parts):
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
        },
        sort_keys=True,
        default=str,
    )


def _exercise_grading_signature(exercise):
    parts = [(p.prompt, p.formula) for p in exercise.parts.all()]
    return _grading_signature(
        exercise.kripke_structure, exercise.allowed_operators,
        exercise.declared_aps, parts,
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
            exercise.exercise_type = new_type
            exercise.parts.all().delete()
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
    # global hints belong to the partless type; part types carry hints per part
    global_hints = form["hints"] if exercise.exercise_type == "model_check" else []
    exercise.hints = global_hints
    exercise.hint = next((h for h in global_hints if h), "")
    exercise.allowed_operators = form["allowed_operators"]
    exercise.declared_aps = form["declared_aps"]
    # build_kripke is student-built — never persist the builder's hidden editor
    exercise.kripke_structure = None if exercise.exercise_type == "build_kripke" else graph
    exercise.is_published = publishing
    if publishing:
        exercise.ever_published = True
    exercise.save()
    if exercise.exercise_type != "model_check":
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
