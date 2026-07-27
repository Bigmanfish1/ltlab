from collections import Counter
from datetime import timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.middleware import (
    supabase_login_required,
    teacher_page,
    teacher_required,
)
from apps.accounts.models import Profile
from apps.exercises import services
from apps.exercises.models import Attempt, Exercise, Topic


@supabase_login_required
def home(request):
    """Dashboard landing page — dispatch to the role-specific view.

    Each dashboard carries its own access decorator (teacher_required for the
    teacher view, supabase_login_required for the student one), so teacher-only
    content is gated by the same mechanism every future page/endpoint uses
    rather than an ad-hoc role check here.
    """
    if request.effective_role == Profile.ROLE_TEACHER:
        return teacher_dashboard(request)
    return student_dashboard(request)


# ---------------------------------------------------------------------------
# Student dashboard
# ---------------------------------------------------------------------------

def topic_display_difficulty(topic):
    """Most common difficulty among a topic's exercises."""
    values = list(
        topic.exercises.filter(is_published=True).values_list('difficulty', flat=True)
    )
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def get_day_streak(student):
    dates = list(
        Attempt.objects.filter(student=student)
        .dates('created_at', 'day')
        .order_by('-created_at')
    )
    if not dates:
        return 0

    streak = 0
    expected_date = timezone.now().date()

    for d in dates:
        if d == expected_date:
            streak += 1
            expected_date -= timedelta(days=1)
        else:
            break

    return streak


@supabase_login_required
def student_dashboard(request):
    student = request.profile

    solved = services.solved_exercise_ids(student)
    completed_by_topic = Counter(
        topic_id
        for topic_id, ex_id in Exercise.objects.filter(
            is_published=True
        ).values_list("topic_id", "id")
        if ex_id in solved
    )

    topics = Topic.objects.annotate(
        total_exercises=Count(
            'exercises', filter=Q(exercises__is_published=True), distinct=True
        ),
    ).order_by('position', 'id')

    modules = []
    for topic in topics:
        completed = completed_by_topic.get(topic.id, 0)
        completion = (
            round((completed / topic.total_exercises) * 100)
            if topic.total_exercises else 0
        )
        if completion == 100:
            status = "complete"
        elif completion > 0:
            status = "in-progress"
        else:
            status = "locked"

        modules.append({
            "id": topic.id,
            "name": topic.title,
            "difficulty": topic_display_difficulty(topic),
            "completion": completion,
            "status": status,
        })

    total_exercises = Exercise.objects.filter(is_published=True).count()
    exercises_done = len(solved)
    overall_progress = round((exercises_done / total_exercises) * 100) if total_exercises else 0

    # scope to published exercises so accuracy shares the denominator population
    # with the progress metrics above (attempts on unpublished drafts excluded)
    attempts = Attempt.objects.filter(student=student, exercise__is_published=True)
    total_attempts = attempts.count()
    correct_attempts = attempts.filter(is_correct=True).count()
    accuracy = round((correct_attempts / total_attempts) * 100) if total_attempts else 0

    context = {
        "modules": modules,
        "overall_progress": overall_progress,
        "exercises_done": exercises_done,
        "accuracy": accuracy,
        "day_streak": get_day_streak(student),
    }
    return render(request, "dashboard/student_dashboard.html", context)


# ---------------------------------------------------------------------------
# Teacher dashboard / results
# ---------------------------------------------------------------------------

@teacher_required
def teacher_dashboard(request):
    stats = services.dashboard_stats()
    context = {
        "teacher_name": request.profile.name or request.profile.email,
        "stats": [
            {"label": "STUDENTS ENROLLED", "value": stats["students_enrolled"], "delta": "", "positive": True, "href": reverse("results") + "?section=roster"},
            {"label": "ACTIVE THIS WEEK", "value": stats["active_this_week"], "delta": "", "positive": True},
            {"label": "CLASS ACCURACY", "value": f"{stats['class_accuracy']}%", "delta": "", "positive": True},
        ],
        "activity": services.recent_activity(),
        "quick_actions": [],
    }
    return render(request, "dashboard/teacher_dashboard.html", context)


@teacher_page()
def teacher_results(request):
    data = services.results_data()
    metrics = data["metrics"]
    context = {
        "metrics": [
            {"label": "TOTAL STUDENTS", "value": str(metrics["total_students"])},
            {"label": "AVG ACCURACY", "value": f"{metrics['avg_accuracy']}%"},
            {"label": "MOST FAILED EXERCISE", "value": metrics["most_failed_exercise"], "compact": True},
            {"label": "AVG ATTEMPTS / EX", "value": str(metrics["avg_attempts_per_ex"])},
        ],
        "module_completion": data["module_completion"],
        "struggled_exercises": data["struggled_exercises"],
        "misconceptions": data["misconceptions"],
        "students": data["students"],
    }
    return render(request, "results/teacher_results.html", context)


@teacher_page()
def teacher_student_detail(request, student_id):
    student = get_object_or_404(Profile, pk=student_id, role=Profile.ROLE_STUDENT)
    return render(request, "results/teacher_student_detail.html", services.student_detail(student))
