import json
import math
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import Profile
from apps.checker.misconceptions import classify_misconception

from .models import Attempt, Exercise, Topic

MISCONCEPTION_LABELS = {
    "g_vs_f": "G vs F confusion",
    "f_vs_x": "F vs X confusion",
    "missing_global": "Missing G (always)",
    "missing_eventually": "Missing F (eventually)",
    "inverted": "Inverted property",
    "mistranslation": "English to LTL translation",
}

MISCONCEPTION_DESCRIPTIONS = {
    "g_vs_f": "swapped G (always) and F (eventually) — e.g. FG vs GF, or always vs eventually",
    "f_vs_x": "used X (next) where F (eventually) was required, or vice versa",
    "missing_global": "omitted the G (always) that the specification requires",
    "missing_eventually": "omitted the F (eventually) that the specification requires",
    "inverted": "expressed the negation of the target property",
    "mistranslation": "mistranslated the plain-English requirement into LTL",
}


def enrolled_students():
    return Profile.objects.filter(role=Profile.ROLE_STUDENT)


def _pct(n, d):
    # round half up; n, d are non-negative counts here so floor(x+0.5) is exact
    return math.floor(100 * n / d + 0.5) if d else 0


def _attempt_matrix():
    # numerator population must match the enrolled denominator, else completion can exceed 100%
    enrolled_ids = enrolled_students().values_list("id", flat=True)
    matrix = defaultdict(lambda: defaultdict(list))
    rows = Attempt.objects.filter(student_id__in=enrolled_ids).values_list(
        "exercise_id", "student_id", "is_correct", "created_at", "formula_input", "hints_used"
    ).order_by("created_at")
    for ex_id, st_id, correct, created, formula, hints in rows:
        matrix[ex_id][st_id].append(
            {"correct": correct, "created": created, "formula": formula, "hints": hints}
        )
    return matrix


def _exercise_metrics(exercise, per_student, enrolled_count):
    total_attempts = sum(len(a) for a in per_student.values())
    solvers = 0
    tries_to_solve = []
    for attempts in per_student.values():
        first_correct = next((i for i, a in enumerate(attempts) if a["correct"]), None)
        if first_correct is not None:
            solvers += 1
            tries_to_solve.append(first_correct + 1)
    engaged = len(per_student)
    completion_raw = 100 * solvers / enrolled_count if enrolled_count else 0.0
    completion = _pct(solvers, enrolled_count)
    avg_tries = round(sum(tries_to_solve) / len(tries_to_solve), 1) if tries_to_solve else 0.0
    fail_attempts = total_attempts - sum(
        1 for attempts in per_student.values() for a in attempts if a["correct"]
    )
    # struggle = mean submissions per engaged student; unsolved exercises rank high (unlike avg_tries)
    struggle = round(total_attempts / engaged, 1) if engaged else 0.0
    return {
        "attempts": total_attempts,
        "solvers": solvers,
        "completion": completion,
        "completion_raw": completion_raw,
        "avg_tries": avg_tries,
        "fail_attempts": fail_attempts,
        "struggle": struggle,
    }


def exercise_rows():
    matrix = _attempt_matrix()
    enrolled_count = enrolled_students().count()
    rows = []
    for ex in Exercise.objects.select_related("topic"):
        m = _exercise_metrics(ex, matrix.get(ex.id, {}), enrolled_count)
        rows.append({
            "id": ex.id,
            "name": ex.title,
            "module": ex.topic.title,
            "difficulty": ex.difficulty,
            "attempts": m["attempts"],
            "completion": m["completion"],
            "completion_raw": m["completion_raw"],
            "avg_tries": m["avg_tries"],
            "struggle": m["struggle"],
        })
    return rows


def topic_completion():
    rows = exercise_rows()
    by_topic = defaultdict(list)
    for r in rows:
        by_topic[r["module"]].append(r["completion_raw"])
    out = []
    for topic in Topic.objects.all():
        comps = by_topic.get(topic.title, [])
        # average the raw rates, round once (avoids compounding per-exercise rounding)
        out.append({"name": topic.title, "completion": round(sum(comps) / len(comps)) if comps else 0})
    return out


def class_metrics():
    matrix = _attempt_matrix()
    enrolled_count = enrolled_students().count()
    total = correct = exercises_with_attempts = 0
    most_failed = None
    most_failed_rate = -1.0
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
        "most_failed_exercise": most_failed or "—",
        "avg_attempts_per_ex": round(total / exercises_with_attempts, 1) if exercises_with_attempts else 0.0,
    }


def struggled_exercises(limit=5):
    rows = [r for r in exercise_rows() if r["attempts"]]
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


def misconception_breakdown():
    counts = defaultdict(int)
    total_wrong = 0
    rows = Attempt.objects.filter(is_correct=False).values_list(
        "formula_input", "exercise__target_formula"
    )
    for submitted, target in rows:
        bucket = classify_misconception(target, submitted)
        if bucket:
            counts[bucket] += 1
            total_wrong += 1
    pcts = _largest_remainder(counts, total_wrong)
    out = []
    for bucket, n in counts.items():
        pct = pcts.get(bucket, 0)
        out.append({
            "key": bucket,
            "label": MISCONCEPTION_LABELS[bucket],
            "description": f"{pct}% of incorrect submissions {MISCONCEPTION_DESCRIPTIONS[bucket]}",
            "percentage": pct,
        })
    out.sort(key=lambda x: x["percentage"], reverse=True)
    return out


def _largest_remainder(counts, total):
    # apportion integer percentages that sum to exactly 100 (Hamilton method)
    if not total:
        return {}
    raw = {b: 100 * n / total for b, n in counts.items()}
    floors = {b: int(v) for b, v in raw.items()}
    leftover = 100 - sum(floors.values())
    for b, _ in sorted(raw.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)[:leftover]:
        floors[b] += 1
    return floors


def students_roster():
    matrix = _attempt_matrix()
    per_student = defaultdict(lambda: {"total": 0, "correct": 0, "solved": set(), "last": None})
    for ex_id, students in matrix.items():
        for st_id, attempts in students.items():
            acc = per_student[st_id]
            acc["total"] += len(attempts)
            for a in attempts:
                if a["correct"]:
                    acc["correct"] += 1
                    acc["solved"].add(ex_id)
                if acc["last"] is None or a["created"] > acc["last"]:
                    acc["last"] = a["created"]
    out = []
    for student in enrolled_students():
        acc = per_student.get(student.id)
        if acc:
            out.append({
                "id": student.id,
                "name": student.name or student.email,
                "exercises_done": len(acc["solved"]),
                "accuracy": _pct(acc["correct"], acc["total"]),
                "last_active": _humanize(acc["last"]),
            })
        else:
            out.append({
                "id": student.id,
                "name": student.name or student.email,
                "exercises_done": 0,
                "accuracy": 0,
                "last_active": "never",
            })
    out.sort(key=lambda s: s["exercises_done"], reverse=True)
    return out


def dashboard_stats():
    roster = students_roster()
    metrics = class_metrics()
    week_ago = timezone.now() - timedelta(days=7)
    enrolled_ids = enrolled_students().values_list("id", flat=True)
    active = (
        Attempt.objects.filter(created_at__gte=week_ago, student_id__in=enrolled_ids)
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
    rows = Attempt.objects.select_related("student", "exercise", "exercise__topic").order_by("-created_at")[:limit]
    for a in rows:
        name = a.student.name or a.student.email
        initials = "".join(p[0] for p in name.split()[:2]).upper() or "?"
        verb = "completed" if a.is_correct else "attempted"
        out.append({
            "student_id": a.student_id,
            "initials": initials,
            "text": f"{name} {verb} {a.exercise.title} · {a.exercise.topic.title}",
            "time": _humanize(a.created_at),
            "type": "done" if a.is_correct else "stuck",
        })
    return out


def student_detail(student):
    attempts = list(
        Attempt.objects.filter(student=student)
        .select_related("exercise")
        .order_by("-created_at")
    )
    total = len(attempts)
    correct = sum(1 for a in attempts if a.is_correct)
    solved = {a.exercise_id for a in attempts if a.is_correct}
    hints_used = sum(a.hints_used for a in attempts)
    history = [{
        "exercise": a.exercise.title,
        "formula": a.formula_input or "",
        "result": a.is_correct,
        "hints": a.hints_used,
        "date": _short_date(a.created_at),
    } for a in attempts[:12]]
    last = attempts[0] if attempts else None
    last_submission = None
    if last is not None:
        structure = last.exercise.kripke_structure
        elements = structure.get("elements") if isinstance(structure, dict) else {}
        array = (elements.get("nodes") or []) + (elements.get("edges") or []) if elements else []
        last_submission = {
            "formula": last.formula_input or "",
            "verdict": "Property holds." if last.is_correct else "Property violated.",
            "holds": last.is_correct,
            "elements_json": json.dumps(array) if array else "",
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
    return dt.strftime("%d %b") if dt else ""
