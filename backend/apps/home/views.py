from django.shortcuts import render

from apps.accounts.middleware import supabase_login_required


@supabase_login_required
def home(request):
    # supabase_login_required guarantees request.profile is set (it bounces the
    # authenticated-but-no-Profile state to re-login).
    modules_data = [
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
    ]

    context = {
        # Student dashboard
        "modules": modules_data,
        "overall_progress": 47,
        "exercises_done": 24,
        "accuracy": 91,
        "day_streak": 6,

        # Teacher dashboard
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

    role = request.profile.role
    template = "dashboard/teacher_dashboard.html" if role == "teacher" else "dashboard/student_dashboard.html"
    return render(request, template, context)
