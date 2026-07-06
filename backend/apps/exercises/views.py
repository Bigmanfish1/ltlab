import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.middleware import supabase_login_required, teacher_required
from apps.checker.tasks import run_ltl_check

from .models import Exercise, Topic
from .services import _elements_json, exercise_rows


# Mock data for testing
MOCK_KRIPKE_MODEL = {
    'id': 1,
    'description': 'A basic traffic light system',
    'states': [
        {'id': 's0', 'label': 'Green', 'props': ['green']},
        {'id': 's1', 'label': 'Yellow', 'props': ['yellow']},
        {'id': 's2', 'label': 'Red', 'props': ['red']}
    ],
    'transitions': [
        {'source': 's0', 'target': 's1'},
        {'source': 's1', 'target': 's2'},
        {'source': 's2', 'target': 's0'}
    ],
    'initial_state': 's0'
}

MOCK_EXERCISES = [
    {
        'id': 1,
        'title': 'Exercise 01 · Always Eventually Green',
        'description': 'Write a formula that states the traffic light will always eventually turn green.',
        'correct_formula': 'G F green',
        'hints': [
            'Think about the "always" operator (G)',
            'Combine it with the "eventually" operator (F)',
            'The complete formula is: G F green'
        ],
        'kripke_model': MOCK_KRIPKE_MODEL
    },
    {
        'id': 2,
        'title': 'Exercise 02 · Never Red and Green',
        'description': 'Write a formula stating that red and green can never be true at the same time.',
        'correct_formula': 'G !(red & green)',
        'hints': [
            'Use the negation operator (!)',
            'Use the "always" operator (G)',
            'Think about when both propositions are true together'
        ],
        'kripke_model': MOCK_KRIPKE_MODEL
    },
    {
        'id': 3,
        'title': 'Exercise 03 · Yellow Leads to Red',
        'description': 'Write a formula stating that whenever yellow is true, red must eventually follow.',
        'correct_formula': 'G (yellow -> F red)',
        'hints': [
            'Use the implication operator (->)',
            'Combine with the eventually operator (F)',
            'Wrap everything in always (G)'
        ],
        'kripke_model': MOCK_KRIPKE_MODEL
    },
    {
        'id': 4,
        'title': 'Exercise 04 · Next State Property',
        'description': 'Write a formula using the next operator.',
        'correct_formula': 'X green',
        'hints': ['Use the X (next) operator'],
        'kripke_model': MOCK_KRIPKE_MODEL
    }
]

MOCK_ATTEMPTS = [
    {
        'id': 1,
        'submitted_formula': 'F G green',
        'is_correct': False,
        'submitted_at': '2 hours ago',
    },
    {
        'id': 2,
        'submitted_formula': 'G green',
        'is_correct': False,
        'submitted_at': '1 hour ago',
    },
    {
        'id': 3,
        'submitted_formula': 'G F green',
        'is_correct': True,
        'submitted_at': '30 minutes ago',
    }
]


def get_mock_exercise(exercise_id):
    """Get mock exercise by ID"""
    for exercise in MOCK_EXERCISES:
        if exercise['id'] == exercise_id:
            return exercise
    return None

@supabase_login_required
def exercises(request):
    exercises_data = []
    for exercise in MOCK_EXERCISES:
        exercises_data.append({
            'exercise': exercise,
            'is_completed': exercise['id'] == 1,  # Mock: only exercise with ID 1 is completed
            'attempt_count': exercise['id'],  # Mock: attempt count = exercise ID
            'best_attempt': None,
        })

    context = {
        'exercises_data': exercises_data,
    }
    return render(request, 'exercises/exercises.html', context)


@supabase_login_required
def exercise_canvas(request, exercise_id):
    """Exercise canvas with Kripke model, formula input, and submission"""
    exercise = get_mock_exercise(exercise_id)

    if not exercise:
        return render(request, '404.html', status=404)

    # Get mock attempts for this exercise
    attempts = MOCK_ATTEMPTS if exercise_id == 1 else []

    # Mock completion status
    is_completed = any(a['is_correct'] for a in attempts)

    # Find previous and next exercises
    all_exercises = MOCK_EXERCISES

    current_index = next((i for i, ex in enumerate(all_exercises) if ex['id'] == exercise_id), 0)
    prev_exercise = all_exercises[current_index - 1] if current_index > 0 else None
    next_exercise = all_exercises[current_index + 1] if current_index < len(all_exercises) - 1 else None

    context = {
        'exercise': exercise,
        'kripke_model': exercise['kripke_model'],
        'attempts': attempts,
        'is_completed': is_completed,
        'prev_exercise': prev_exercise,
        'next_exercise': next_exercise
    }
    return render(request, 'exercises/exercise_canvas.html', context)


@supabase_login_required
@require_POST
def submit_formula(request, exercise_id):
    """Handle formula submission and check correctness"""
    exercise = get_mock_exercise(exercise_id)

    if not exercise:
        return JsonResponse({'error': 'Exercise not found'}, status=404)

    try:
        data = json.loads(request.body)
        submitted_formula = data.get('formula', '').strip()
        time_spent = data.get('time_spent', 0)
        hints_used = data.get('hints_used', 0)

        if not submitted_formula:
            return JsonResponse({'error': 'Formula cannot be empty'}, status=400)

        # Check if formula is correct (submitted_formula already stripped above)
        is_correct = submitted_formula == exercise['correct_formula'].strip()

        # Generate counterexample if incorrect
        counterexample = None
        if not is_correct:
            counterexample = {
                'path': ['s0', 's1', 's2', 's0'],
                'reason': f'The formula "{submitted_formula}" does not hold on this path. Expected: {exercise["correct_formula"]}',
                'violated_at': 's1'
            }

        return JsonResponse({
            'success': True,
            'is_correct': is_correct,
            'counterexample': counterexample,
            'message': 'Correct! Well done. 🎉' if is_correct else 'Incorrect. Check the counterexample and try again.',
            'attempt_id': 999  # Mock ID
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@supabase_login_required
def get_hint(request, exercise_id):
    """Get next hint for exercise"""
    exercise = get_mock_exercise(exercise_id)

    if not exercise:
        return JsonResponse({'error': 'Exercise not found'}, status=404)

    hint_index = int(request.GET.get('index', 0))

    if hint_index < len(exercise['hints']):
        return JsonResponse({
            'hint': exercise['hints'][hint_index],
            'hint_index': hint_index,
            'total_hints': len(exercise['hints'])
        })
    else:
        return JsonResponse({'error': 'No more hints available'}, status=404)


MOCK_TEACHER_EXERCISES = [
    {"name": "Basic Kripke Structure", "module": "Kripke Structures", "difficulty": "beginner", "attempts": 142, "completion": 92, "avg_tries": 1.4},
    {"name": "Atomic Propositions", "module": "Kripke Structures", "difficulty": "beginner", "attempts": 138, "completion": 95, "avg_tries": 1.2},
    {"name": "Labelling States", "module": "Kripke Structures", "difficulty": "beginner", "attempts": 129, "completion": 89, "avg_tries": 1.5},
    {"name": "Transition Relations", "module": "Kripke Structures", "difficulty": "beginner", "attempts": 121, "completion": 86, "avg_tries": 1.7},
    {"name": "Always Eventually", "module": "LTL Operators", "difficulty": "intermediate", "attempts": 98, "completion": 71, "avg_tries": 2.3},
    {"name": "Until Operator", "module": "LTL Operators", "difficulty": "intermediate", "attempts": 76, "completion": 58, "avg_tries": 2.9},
    {"name": "Weak Until", "module": "LTL Operators", "difficulty": "intermediate", "attempts": 69, "completion": 54, "avg_tries": 3.1},
    {"name": "Release Operator", "module": "LTL Operators", "difficulty": "intermediate", "attempts": 61, "completion": 49, "avg_tries": 3.3},
    {"name": "Request-Grant Protocol", "module": "LTL Operators", "difficulty": "intermediate", "attempts": 87, "completion": 64, "avg_tries": 3.4},
    {"name": "Next-State Reasoning", "module": "LTL Operators", "difficulty": "intermediate", "attempts": 73, "completion": 60, "avg_tries": 2.6},
    {"name": "Mutual Exclusion", "module": "CTL Semantics", "difficulty": "advanced", "attempts": 54, "completion": 38, "avg_tries": 4.2},
    {"name": "Nested Modalities", "module": "CTL Semantics", "difficulty": "advanced", "attempts": 41, "completion": 29, "avg_tries": 3.7},
    {"name": "Path Quantifiers", "module": "CTL Semantics", "difficulty": "advanced", "attempts": 47, "completion": 34, "avg_tries": 3.9},
    {"name": "Existential Until", "module": "CTL Semantics", "difficulty": "advanced", "attempts": 38, "completion": 27, "avg_tries": 4.1},
    {"name": "Fairness Constraints", "module": "Fairness & Liveness", "difficulty": "advanced", "attempts": 32, "completion": 22, "avg_tries": 4.8},
    {"name": "Strong Fairness", "module": "Fairness & Liveness", "difficulty": "advanced", "attempts": 29, "completion": 19, "avg_tries": 4.6},
    {"name": "Liveness Properties", "module": "Fairness & Liveness", "difficulty": "advanced", "attempts": 35, "completion": 24, "avg_tries": 4.4},
    {"name": "Starvation Freedom", "module": "Fairness & Liveness", "difficulty": "advanced", "attempts": 27, "completion": 17, "avg_tries": 4.9},
    {"name": "Counterexample Traces", "module": "Model Refinement", "difficulty": "intermediate", "attempts": 58, "completion": 46, "avg_tries": 3.0},
    {"name": "Abstraction Mapping", "module": "Model Refinement", "difficulty": "advanced", "attempts": 33, "completion": 21, "avg_tries": 4.5},
]

BUILDER_OPERATORS = ["G", "F", "X", "U", "¬", "∧", "∨", "→"]
DIFFICULTIES = ["beginner", "intermediate", "advanced"]


@teacher_required
def teacher_exercises(request):
    return render(request, "exercises/teacher_exercises.html", {
        "exercises": exercise_rows(),
    })


@teacher_required
def manage(request):
    topics = list(Topic.objects.prefetch_related("exercises"))
    modules = []
    for i, t in enumerate(topics, start=1):
        modules.append({
            "id": t.id,
            "index": f"{i:02d}",
            "title": t.title,
            "unlocks_after": t.unlocks_after.title if t.unlocks_after_id else "None",
            "visible": t.visible,
            "exercises": [
                {"id": e.id, "name": e.title, "difficulty": e.difficulty}
                for e in t.exercises.all()
            ],
        })
    return render(request, "manage/teacher_manage.html", {
        "modules": modules,
        "topics": topics,
    })


def _builder_context(exercise, form=None):
    if form is not None:
        hint_values = form["hints"]
        allowed = form["allowed_operators"]
        try:
            elements_json = _elements_json(json.loads(form["graph_data"]) if form["graph_data"] else None)
        except json.JSONDecodeError:
            elements_json = ""
        prefill = form
    elif exercise is not None:
        hints = list(exercise.hints or [])[:3]
        hint_values = hints + [""] * (3 - len(hints))
        allowed = exercise.allowed_operators or BUILDER_OPERATORS
        elements_json = _elements_json(exercise.kripke_structure)
        prefill = {
            "title": exercise.title,
            "description": exercise.description,
            "difficulty": exercise.difficulty,
            "module_id": exercise.topic_id,
            "target_formula": exercise.target_formula,
        }
    else:
        hint_values = ["", "", ""]
        allowed = list(BUILDER_OPERATORS)
        elements_json = ""
        prefill = None
    return {
        "modules": list(Topic.objects.all()),
        "operators": BUILDER_OPERATORS,
        "difficulties": DIFFICULTIES,
        "hint_values": hint_values,
        "allowed_operators": allowed,
        "elements_json": elements_json,
        "prefill": prefill,
        "selected_topic_id": prefill["module_id"] if prefill else None,
        "is_edit": exercise is not None,
        "exercise_id": exercise.id if exercise else None,
    }


@teacher_required
def exercise_builder(request, exercise_id=None):
    exercise = get_object_or_404(Exercise, pk=exercise_id) if exercise_id else None

    if request.method == "POST":
        return _save_exercise(request, exercise)

    context = _builder_context(exercise)
    if exercise is None:
        tid = request.GET.get("topic", "")
        if tid.isdigit() and Topic.objects.filter(pk=tid).exists():
            context["selected_topic_id"] = int(tid)
    return render(request, "exercises/teacher_exercise_builder.html", context)


def _save_exercise(request, exercise):
    action = request.POST.get("action", "draft")
    title = request.POST.get("title", "").strip()
    description = request.POST.get("description", "").strip()
    difficulty = request.POST.get("difficulty", "").strip()
    topic_id = request.POST.get("topic", "").strip()
    target_formula = request.POST.get("formula", "").strip()
    graph_data = request.POST.get("graph_data", "").strip()
    hints = [request.POST.get(f"hint_{i}", "").strip() for i in (1, 2, 3)]
    try:
        allowed = json.loads(request.POST.get("allowed_operators") or "[]")
    except json.JSONDecodeError:
        allowed = []

    form = {
        "title": title,
        "description": description,
        "difficulty": difficulty,
        "module_id": int(topic_id) if topic_id.isdigit() else None,
        "target_formula": target_formula,
        "hints": hints,
        "allowed_operators": allowed,
        "graph_data": graph_data,
    }

    errors = []
    if not title:
        errors.append("Exercise title is required.")
    if not description:
        errors.append("Task description is required.")
    if difficulty not in DIFFICULTIES:
        errors.append("Select a difficulty.")
    if not topic_id.isdigit() or not Topic.objects.filter(pk=topic_id).exists():
        errors.append("Assign the exercise to a module.")
    if not target_formula:
        errors.append("Solution formula is required.")

    publishing = action == "publish"
    graph = None
    if graph_data:
        try:
            graph = json.loads(graph_data)
        except json.JSONDecodeError:
            errors.append("The Kripke structure could not be read.")
    elif exercise is not None:
        graph = exercise.kripke_structure

    if publishing and not errors:
        if not graph:
            errors.append("Publishing needs a memorandum Kripke structure.")
        else:
            try:
                result = run_ltl_check(graph, target_formula)
            except ValueError as exc:
                errors.append(f"Formula check failed: {exc}")
            else:
                if result["result"] != "satisfied":
                    errors.append("The solution formula does not hold on the memorandum structure.")

    if errors:
        context = _builder_context(exercise, form)
        context["form_errors"] = errors
        return render(request, "exercises/teacher_exercise_builder.html", context)

    if exercise is None:
        exercise = Exercise(topic_id=form["module_id"], created_at=timezone.now())
    else:
        exercise.topic_id = form["module_id"]
    exercise.title = title
    exercise.description = description
    exercise.difficulty = difficulty
    exercise.target_formula = target_formula
    exercise.hints = hints
    exercise.hint = next((h for h in hints if h), "")
    exercise.allowed_operators = allowed
    exercise.kripke_structure = graph
    exercise.is_published = publishing
    exercise.save()

    messages.success(request, "Exercise published." if publishing else "Draft saved.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_create(request):
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Module title is required.")
        return redirect("manage")
    unlocks_id = request.POST.get("unlocks_after", "").strip()
    unlocks = Topic.objects.filter(pk=unlocks_id).first() if unlocks_id.isdigit() else None
    position = (Topic.objects.count())
    Topic.objects.create(
        title=title,
        description=request.POST.get("description", "").strip(),
        visible=request.POST.get("visible") == "1",
        unlocks_after=unlocks,
        position=position,
        created_by=request.profile,
    )
    messages.success(request, "Module created.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_delete(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    topic.delete()
    messages.success(request, "Module deleted.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_visibility(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    topic.visible = not topic.visible
    topic.save(update_fields=["visible"])
    return JsonResponse({"visible": topic.visible})


@teacher_required
@require_POST
def exercise_delete(request, exercise_id):
    exercise = get_object_or_404(Exercise, pk=exercise_id)
    exercise.delete()
    messages.success(request, "Exercise deleted.")
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("manage")


@teacher_required
@require_POST
def topic_reorder(request):
    try:
        order = json.loads(request.POST.get("order") or "[]")
    except json.JSONDecodeError:
        order = []
    for pos, tid in enumerate(order):
        Topic.objects.filter(pk=tid).update(position=pos)
    return JsonResponse({"ok": True})
