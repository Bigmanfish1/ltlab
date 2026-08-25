from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from apps.accounts.models import Profile

from ..constants import EXERCISE_TYPE_BADGES, MISCONCEPTION_LABELS
from ..models import Attempt, Exercise, ExercisePart, Topic
from .common import _elements_json, _humanize, _pct, _round_half_up, _short_date


def enrolled_students():
    return Profile.objects.filter(role=Profile.ROLE_STUDENT).order_by("name", "id")


def enrolled_ids():
    return enrolled_students().values_list("id", flat=True)


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


# build_kripke stores its requirements as parts, but a submission is graded as
# one whole model and recorded as a single partless attempt. Reporting it as
# part-based leaves it permanently unsolved in every teacher metric.
WHOLE_GRADED_TYPES = ("build_kripke",)


def _part_counts():
    return dict(
        ExercisePart.objects.exclude(exercise__exercise_type__in=WHOLE_GRADED_TYPES)
        .values("exercise_id")
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
    whole_exercise_ids = set(
        Exercise.objects.filter(id__in=ids, exercise_type__in=WHOLE_GRADED_TYPES)
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
    cycle_str = "(" + ", ".join(cycle) + ")ω"
    return ", ".join(prefix) + ", " + cycle_str if prefix else cycle_str


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
