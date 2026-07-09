import json
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.middleware import supabase_login_required, teacher_required
from .models import Exercise, Attempt

def get_exercise(exercise_id):
    exercise = Exercise.objects.filter(id=exercise_id).first()
    return exercise

@supabase_login_required
def exercises(request):
    exercises_data = []
    data = Exercise.objects.all()
    for exercise in data:
        attempt_count = Attempt.objects.filter(exercise=exercise, student=request.supabase_user.id).count()
        is_completed = Attempt.objects.filter(exercise=exercise, student=request.supabase_user.id, is_correct=True).exists()
        exercises_data.append({
            'exercise': exercise,
            'is_completed': is_completed,
            'attempt_count': attempt_count,
            'best_attempt': None,
        })

    context = {
        'exercises_data': exercises_data,
    }
    return render(request, 'exercises/exercises.html', context)


@supabase_login_required
def exercise_canvas(request, exercise_id):
    """Exercise canvas with Kripke model, formula input, and submission"""
    exercise = get_exercise(exercise_id)

    if not exercise:
        return render(request, '404.html', status=404)

    attempts = Attempt.objects.filter(exercise=exercise, student=request.supabase_user.id)
    is_completed = Attempt.objects.filter(exercise=exercise, student=request.supabase_user.id, is_correct=True).exists()


    # Find previous and next exercises
    all_exercises = Exercise.objects.all()

    current_index = next((i for i, ex in enumerate(all_exercises) if ex.id == exercise_id), 0)
    prev_exercise = all_exercises[current_index - 1] if current_index > 0 else None
    next_exercise = all_exercises[current_index + 1] if current_index < len(all_exercises) - 1 else None

    context = {
        'exercise': exercise,
        'exercise_number': current_index + 1,
        'kripke_model': "",
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
    exercise = get_object_or_404(Exercise, id=exercise_id)
    student = request.profile  # confirm this matches your middleware's attribute name

    try:
        data = json.loads(request.body)
        submitted_formula = data.get('formula', '').strip()

        if not submitted_formula:
            return JsonResponse({'error': 'Formula cannot be empty'}, status=400)

        is_correct = submitted_formula == exercise.target_formula.strip()

        counterexample = None
        if not is_correct:
            counterexample = {
                'path': ['s0', 's1', 's2', 's0'],
                'reason': f'The formula "{submitted_formula}" does not hold on this path. Expected: {exercise.target_formula}',
                'violated_at': 's1',
            }

        attempt = Attempt.objects.create(
            exercise=exercise,
            student=student,
            formula_input=submitted_formula,
            is_correct=is_correct,
        )

        return JsonResponse({
            'success': True,
            'is_correct': is_correct,
            'counterexample': counterexample,
            'message': 'Correct! Well done. 🎉' if is_correct else 'Incorrect. Check the counterexample and try again.',
            'attempt_id': str(attempt.id),
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@supabase_login_required
def get_hint(request, exercise_id):
    """Get next hint for exercise"""
    exercise = get_exercise(exercise_id)

    if exercise.hint != "":
        return JsonResponse({
            'hint': exercise.hint
        })
    else:
        return JsonResponse({'error': 'No hint available'}, status=404)


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

@teacher_required
def teacher_exercises(request):
    return render(request, 'exercises/teacher_exercises.html', {
        'exercises': MOCK_TEACHER_EXERCISES,
    })
