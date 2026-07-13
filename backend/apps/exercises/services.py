import json
import math
from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.models import Profile
from apps.checker.engine import validate_request
from apps.checker.equivalence import validate_formula_submission
from apps.checker.misconceptions import classify_misconception
from apps.checker.tasks import run_ltl_check
from apps.checker.views import _PROP_NAME_RE, _RESERVED_PROP_NAMES

from .constants import DIFFICULTIES, MISCONCEPTION_DESCRIPTIONS, MISCONCEPTION_LABELS
from .models import Attempt, Exercise, ExercisePart, Topic

BUILDER_EXERCISE_TYPES = ("model_check", "english_to_formula", "path_exhibit", "judge")


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


def exercise_rows(matrix=None):
    matrix = matrix if matrix is not None else _attempt_matrix()
    enrolled_count = enrolled_students().count()
    rows = []
    for ex in Exercise.objects.select_related("topic"):
        m = _exercise_metrics(ex, matrix.get(ex.id, {}), enrolled_count)
        rows.append({
            "id": ex.id,
            "name": ex.title,
            "module": ex.topic.title,
            "module_id": ex.topic_id,
            "difficulty": ex.difficulty,
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
        if n:
            if len(parts_solved.get(ex_id, ())) >= n:
                solved.add(ex_id)
        elif ex_id in whole:
            solved.add(ex_id)
    return solved


def _backfill_misconceptions():
    """Classify wrong attempts that have no stored bucket yet, one time each.

    Classification needs SPOT (Django-only); prod attempts written by the external
    system arrive with misconception NULL and get classified on first Results view.
    NULL vs "" (classified, no misconception) keeps this idempotent.

    Only formula-writing attempts are classifiable: partless model_check attempts
    (target on the exercise) and english_to_formula part attempts (target on the
    part). Judge/path answers are verdicts and traces, not formulas.
    """
    pending = (
        Attempt.objects.filter(is_correct=False, misconception__isnull=True)
        .filter(
            Q(part__isnull=True, exercise__exercise_type="model_check")
            | Q(part__isnull=False, exercise__exercise_type="english_to_formula")
        )
        .values_list("id", "formula_input", "exercise__target_formula", "part__formula")
    )
    by_bucket = defaultdict(list)
    for aid, submitted, exercise_target, part_target in pending:
        target = part_target if part_target is not None else exercise_target
        by_bucket[classify_misconception(target, submitted) or ""].append(aid)
    for bucket, ids in by_bucket.items():
        Attempt.objects.filter(pk__in=ids).update(misconception=bucket)


def misconception_breakdown():
    _backfill_misconceptions()
    counts = dict(
        Attempt.objects.filter(is_correct=False, student_id__in=enrolled_ids())
        .exclude(misconception__isnull=True)
        .exclude(misconception="")
        .values_list("misconception")
        .annotate(n=Count("id"))
    )
    total_wrong = sum(counts.values())
    pcts = _largest_remainder(counts, total_wrong)
    out = []
    for bucket, n in counts.items():
        pct = pcts.get(bucket, 0)
        out.append({
            "key": bucket,
            "label": MISCONCEPTION_LABELS.get(bucket, bucket),
            "description": f"{pct}% of classified errors {MISCONCEPTION_DESCRIPTIONS.get(bucket, '')}",
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


def students_roster(matrix=None):
    matrix = matrix if matrix is not None else _attempt_matrix()
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
                "email": student.email,
                "exercises_done": len(acc["solved"]),
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
    rows = exercise_rows(matrix)
    return {
        "metrics": class_metrics(matrix),
        "module_completion": topic_completion(rows),
        "struggled_exercises": struggled_exercises(rows),
        "misconceptions": misconception_breakdown(),
        "students": students_roster(matrix),
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
        last_submission = {
            "formula": last.formula_input or "",
            "verdict": "Property holds." if last.is_correct else "Property violated.",
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
        "target_formula": request.POST.get("formula", "").strip(),
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

    Computed live by model checking so it can never drift from the graph."""
    key = []
    for i, part in enumerate(exercise.parts.all(), start=1):
        result = run_ltl_check(exercise.kripke_structure, part.formula)
        key.append((i, part.formula, result["result"] == "satisfied"))
    return key


def _validate_path_parts(form, graph, errors):
    """Each formula must parse against the graph AND have a satisfying lasso —
    a counterexample to !(φ) is exactly a satisfying path for φ, so an
    unsatisfiable formula would make the part impossible for students."""
    if not form["parts"]:
        errors.append("Add at least one formula for students to find a path for.")
        return
    for i, part in enumerate(form["parts"], start=1):
        if not part["formula"]:
            errors.append(f"Formula {i} is empty.")
            continue
        try:
            result = run_ltl_check(graph, f"!({part['formula']})")
        except ValueError as exc:
            errors.append(f"Formula {i}: {exc}")
            continue
        if result["result"] != "violated":
            errors.append(
                f"Formula {i} ({part['formula']}) has no satisfying path on this "
                "model — students could never solve it."
            )


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

    exercise_type = exercise.exercise_type if exercise else form["exercise_type"]
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
        elif not graph:
            # Students are graded against this graph (model-checking their
            # formula, or walking their path on it), so publishing needs one.
            errors.append("Publishing needs a memorandum Kripke structure.")
        elif exercise_type == "path_exhibit":
            _validate_path_parts(form, graph, errors)
        elif exercise_type == "judge":
            _validate_judge_parts(form, graph, errors)
    return errors, graph


def persist_exercise(exercise, form, graph, publishing):
    if exercise is None:
        exercise = Exercise(
            topic_id=form["module_id"],
            created_at=timezone.now(),
            exercise_type=form["exercise_type"],
        )
    else:
        # exercise_type is locked after creation — switching types under live
        # parts/attempts has no coherent semantics.
        exercise.topic_id = form["module_id"]
    exercise.title = form["title"]
    exercise.description = form["description"]
    exercise.difficulty = form["difficulty"]
    exercise.target_formula = form["target_formula"] or None
    exercise.hints = form["hints"]
    exercise.hint = next((h for h in form["hints"] if h), "")
    exercise.allowed_operators = form["allowed_operators"]
    exercise.declared_aps = form["declared_aps"]
    exercise.kripke_structure = graph
    exercise.is_published = publishing
    exercise.save()
    if exercise.exercise_type != "model_check":
        _sync_parts(exercise, form["parts"])
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
            if (part.prompt, part.formula, part.position) != (
                data["prompt"], data["formula"], position,
            ):
                part.prompt = data["prompt"]
                part.formula = data["formula"]
                part.position = position
                part.save(update_fields=["prompt", "formula", "position"])
        else:
            part = ExercisePart.objects.create(
                exercise=exercise,
                position=position,
                prompt=data["prompt"],
                formula=data["formula"],
            )
            kept_ids.add(str(part.id))
    exercise.parts.exclude(id__in=kept_ids).delete()
