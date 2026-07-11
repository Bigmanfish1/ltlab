import json

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.middleware import supabase_login_required, teacher_required

from .constants import BUILDER_OPERATORS, DIFFICULTIES
from .models import Exercise, Topic
from .services import (
    _elements_json,
    exercise_rows,
    parse_exercise_form,
    persist_exercise,
    validate_exercise_form,
)


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
            "description": t.description or "",
            "unlocks_after": t.unlocks_after.title if t.unlocks_after_id else "None",
            "unlocks_after_id": t.unlocks_after_id or "",
            "visible": t.visible,
            "exercises": [
                {"id": e.id, "name": e.title, "difficulty": e.difficulty, "is_published": e.is_published}
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
        allowed = (
            exercise.allowed_operators
            if exercise.allowed_operators is not None
            else BUILDER_OPERATORS
        )
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
    publishing = request.POST.get("action", "draft") == "publish"
    form = parse_exercise_form(request)
    errors, graph = validate_exercise_form(form, exercise, publishing)
    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, "exercises/teacher_exercise_builder.html", _builder_context(exercise, form))

    persist_exercise(exercise, form, graph, publishing)
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
    highest = Topic.objects.aggregate(m=Max("position"))["m"]
    position = (highest if highest is not None else -1) + 1
    try:
        with transaction.atomic():
            Topic.objects.create(
                title=title,
                description=request.POST.get("description", "").strip(),
                visible=request.POST.get("visible") == "1",
                unlocks_after=unlocks,
                position=position,
                created_by=request.profile,
            )
    except IntegrityError:
        messages.error(request, "A module with that name already exists.")
        return redirect("manage")
    messages.success(request, "Module created.")
    return redirect("manage")


@teacher_required
@require_POST
def topic_update(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Module title is required.")
        return redirect("manage")
    unlocks_id = request.POST.get("unlocks_after", "").strip()
    unlocks = Topic.objects.filter(pk=unlocks_id).first() if unlocks_id.isdigit() else None
    if unlocks and unlocks.id == topic.id:
        unlocks = None
    topic.title = title
    topic.description = request.POST.get("description", "").strip()
    topic.visible = request.POST.get("visible") == "1"
    topic.unlocks_after = unlocks
    try:
        with transaction.atomic():
            topic.save()
    except IntegrityError:
        messages.error(request, "A module with that name already exists.")
        return redirect("manage")
    messages.success(request, "Module updated.")
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
