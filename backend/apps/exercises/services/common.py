import json
import math

from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import Topic

BUILDER_EXERCISE_TYPES = (
    "model_check", "english_to_formula", "path_exhibit", "judge", "build_kripke",
    "buchi_construct", "buchi_word",
)


def _round_half_up(x):
    # Python's round() is half-to-even; we want half-up. x is non-negative here.
    return math.floor(x + 0.5)


def _pct(n, d):
    return _round_half_up(100 * n / d) if d else 0


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
