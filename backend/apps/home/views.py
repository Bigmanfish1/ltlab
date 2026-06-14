from django.shortcuts import render

from apps.accounts.middleware import supabase_login_required, teacher_required
from apps.accounts.models import Profile


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
    context = {
        "teacher_name": "Dr Timm",
        "stats": [
            {
                "label":    "STUDENTS ENROLLED",
                "value":    42,
                "delta":    "+3 this week",
                "positive": True,
            },
            {
                "label":    "ACTIVE THIS WEEK",
                "value":    38,
                "delta":    "+5 vs last week",
                "positive": True,
            },
            {
                "label":    "CLASS ACCURACY",
                "value":    "84%",
                "delta":    "-2% vs last week",
                "positive": False,
            },
        ],
        "activity": [
            {
                "initials": "AD",
                "text":     "Amara Dlamini completed Exercise 05 · LTL Operators",
                "time":     "12m ago",
                "type":     "done",
            },
            {
                "initials": "SN",
                "text":     "Sipho Ndlovu has not progressed past Exercise 03 in 4 days",
                "time":     "1h ago",
                "type":     "stuck",
            },
            {
                "initials": "JK",
                "text":     "Jamie Kim completed Exercise 04 · Kripke Structures",
                "time":     "2h ago",
                "type":     "done",
            },
            {
                "initials": "RW",
                "text":     "Riley Wong stuck on Exercise 07 · CTL Semantics",
                "time":     "3h ago",
                "type":     "stuck",
            },
            {
                "initials": "TP",
                "text":     "Thabo Pillay completed Exercise 06 · Fairness",
                "time":     "5h ago",
                "type":     "done",
            },
            {
                "initials": "LM",
                "text":     "Lerato Mokoena completed Exercise 02 · Kripke Structures",
                "time":     "yesterday",
                "type":     "done",
            },
        ],
        "quick_actions": [
            {"label": "Create New Exercise",  "url": "#"},
            {"label": "Manage Modules",       "url": "#"},
            {"label": "View Full Analytics",  "url": "#"},
        ],
    }
    return render(request, "dashboard/teacher_dashboard.html", context)
