from collections import Counter
from datetime import timedelta

from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from apps.accounts.middleware import supabase_login_required, teacher_required
from apps.accounts.models import Profile
from apps.exercises.models import Attempt, Exercise, Topic


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


def topic_display_difficulty(topic):
    """Most common difficulty among a topic's exercises."""
    values = list(topic.Exercises.values_list('difficulty', flat=True))
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
    student = Profile.objects.get(id=request.supabase_user.id)

    topics = Topic.objects.annotate(
        total_exercises=Count('Exercises', distinct=True),
        completed_exercises=Count(
            'Exercises',
            filter=Q(Exercises__Attempts__student=student, Exercises__Attempts__is_correct=True),
            distinct=True,
        ),
    ).order_by('id')

    modules = []
    for topic in topics:
        completion = (
            round((topic.completed_exercises / topic.total_exercises) * 100)
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

    total_exercises = Exercise.objects.count()
    exercises_done = Exercise.objects.filter(
        Attempts__student=student, Attempts__is_correct=True
    ).distinct().count()
    overall_progress = round((exercises_done / total_exercises) * 100) if total_exercises else 0

    attempts = Attempt.objects.filter(student=student)
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


_MOCK_RESULTS_DATA = {
    "metrics": [
        {"label": "TOTAL STUDENTS", "value": "42"},
        {"label": "AVG ACCURACY", "value": "84%"},
        {"label": "MOST FAILED EXERCISE", "value": "Mutual Exclusion", "compact": True},
        {"label": "AVG ATTEMPTS / EX", "value": "2.4"},
    ],
    "module_completion": [
        {"name": "Kripke Structures", "completion": 92},
        {"name": "LTL Operators", "completion": 71},
        {"name": "CTL Semantics", "completion": 48},
        {"name": "Fairness & Liveness", "completion": 22},
        {"name": "Model Refinement", "completion": 18},
        {"name": "Advanced Patterns", "completion": 9},
    ],
    "struggled_exercises": [
        {"rank": "01", "name": "Mutual Exclusion", "module": "CTL Semantics", "score": 4.2},
        {"rank": "02", "name": "Fairness Constraints", "module": "Fairness & Liveness", "score": 4.8},
        {"rank": "03", "name": "Request-Grant Protocol", "module": "LTL Operators", "score": 3.4},
        {"rank": "04", "name": "Nested Modalities", "module": "CTL Semantics", "score": 3.7},
        {"rank": "05", "name": "Until Operator", "module": "LTL Operators", "score": 2.9},
    ],
    "misconceptions": [
        {"label": "F vs G confusion", "description": "67% of students used F where G was required", "percentage": 67},
        {"label": "X (next) misuse", "description": "42% applied X without considering path semantics", "percentage": 42},
        {"label": "U (until) operator", "description": "38% missed strong-until weak-until distinction", "percentage": 38},
        {"label": "Nested modalities", "description": "29% bracketed nested LTL incorrectly", "percentage": 29},
    ],
    "students": [
        {"name": "Amara Dlamini", "exercises_done": 18, "accuracy": 94, "last_active": "12m ago"},
        {"name": "Sipho Ndlovu", "exercises_done": 6, "accuracy": 62, "last_active": "4 days ago"},
        {"name": "Jamie Kim", "exercises_done": 16, "accuracy": 88, "last_active": "2h ago"},
        {"name": "Riley Wong", "exercises_done": 12, "accuracy": 71, "last_active": "3h ago"},
        {"name": "Thabo Pillay", "exercises_done": 19, "accuracy": 91, "last_active": "5h ago"},
        {"name": "Lerato Mokoena", "exercises_done": 14, "accuracy": 82, "last_active": "yesterday"},
    ],
}


@teacher_required
def teacher_results(request):
    return render(request, "results/teacher_results.html", _MOCK_RESULTS_DATA)
