from django.shortcuts import get_object_or_404, render

from apps.accounts.middleware import supabase_login_required, teacher_required
from apps.accounts.models import Profile
from apps.exercises import services


@supabase_login_required
def home(request):
    """Dashboard landing page — dispatch to the role-specific view.

    Each dashboard carries its own access decorator (teacher_required for the
    teacher view, supabase_login_required for the student one), so teacher-only
    content is gated by the same mechanism every future page/endpoint uses
    rather than an ad-hoc role check here.
    """
    if request.profile.role == Profile.ROLE_TEACHER:
        return teacher_dashboard(request)
    return student_dashboard(request)


@supabase_login_required
def student_dashboard(request):
    context = {
        "modules": [
            {
                "id": 1,
                "name": "Kripke Structures",
                "difficulty": "beginner",
                "completion": 100,
                "status": "complete",
            },
            {
                "id": 2,
                "name": "LTL Operators",
                "difficulty": "intermediate",
                "completion": 64,
                "status": "in-progress",
            },
            {
                "id": 3,
                "name": "CTL Semantics",
                "difficulty": "advanced",
                "completion": 22,
                "status": "in-progress",
            },
            {
                "id": 4,
                "name": "Fairness & Liveness",
                "difficulty": "advanced",
                "completion": 0,
                "status": "locked",
            },
        ],
        "overall_progress": 47,
        "exercises_done": 24,
        "accuracy": 91,
        "day_streak": 6,
    }
    return render(request, "dashboard/student_dashboard.html", context)


@teacher_required
def teacher_dashboard(request):
    stats = services.dashboard_stats()
    context = {
        "teacher_name": request.profile.name or request.profile.email,
        "stats": [
            {"label": "STUDENTS ENROLLED", "value": stats["students_enrolled"], "delta": "", "positive": True},
            {"label": "ACTIVE THIS WEEK", "value": stats["active_this_week"], "delta": "", "positive": True},
            {"label": "CLASS ACCURACY", "value": f"{stats['class_accuracy']}%", "delta": "", "positive": True},
        ],
        "activity": services.recent_activity(),
        "quick_actions": [],
    }
    return render(request, "dashboard/teacher_dashboard.html", context)


@teacher_required
def teacher_results(request):
    metrics = services.class_metrics()
    context = {
        "metrics": [
            {"label": "TOTAL STUDENTS", "value": str(metrics["total_students"])},
            {"label": "AVG ACCURACY", "value": f"{metrics['avg_accuracy']}%"},
            {"label": "MOST FAILED EXERCISE", "value": metrics["most_failed_exercise"], "compact": True},
            {"label": "AVG ATTEMPTS / EX", "value": str(metrics["avg_attempts_per_ex"])},
        ],
        "module_completion": services.topic_completion(),
        "struggled_exercises": services.struggled_exercises(),
        "misconceptions": services.misconception_breakdown(),
        "students": services.students_roster(),
    }
    return render(request, "results/teacher_results.html", context)


@teacher_required
def teacher_student_detail(request, student_id):
    student = get_object_or_404(Profile, pk=student_id)
    return render(request, "results/teacher_student_detail.html", services.student_detail(student))
