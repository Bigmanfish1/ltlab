from django.shortcuts import render


def home(request):
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
        "modules": modules_data,
        "overall_progress": 47,
        "exercises_done": 24,
        "accuracy": 91,
        "day_streak": 6,
    }

    return render(request, "home.html", context)


def sandbox(request):
    return render(request, "sandbox/sandbox.html")
