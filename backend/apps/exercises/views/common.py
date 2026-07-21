from ..models import Exercise
from ..services import solved_exercise_ids


def published_exercises():
    """Exercises visible to students — drafts (is_published=False) are excluded."""
    return Exercise.objects.filter(is_published=True).order_by(
        "topic__position", "position", "id"
    )


def _clamped_hints(request, hints):
    hint_count = len([h for h in (hints or []) if h and h.strip()])
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
