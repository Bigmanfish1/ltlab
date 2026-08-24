import json
import logging

from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from apps.accounts.middleware import supabase_login_required
from apps.checker.operators import disallowed_operators
from apps.checker.tasks import (
    run_buchi_determinism_check,
    run_buchi_equivalence_check,
    run_buchi_word_check,
    run_equivalence_check,
    run_ltl_check,
    run_trace_check,
)
from apps.checker.views import (
    MAX_FORMULA_CHARS,
    MAX_NODES,
    build_result_context,
    error_response,
)

from ..constants import operator_label
from ..models import Attempt, ExercisePart
from .common import (
    _clamped_hints,
    add_trigger,
    _completion_trigger,
    graded_trigger,
    published_exercises,
)

logger = logging.getLogger(__name__)


def _rejected(request, message):
    """A refused submission — sounds the same as a wrong answer."""
    return graded_trigger(error_response(request, message), False)


def _operator_rejection(exercise, bad):
    """Name the operators the student used and the ones they may use instead."""
    used = ", ".join(sorted(operator_label(t) for t in bad))
    allowed = ", ".join(exercise.allowed_operators or [])
    message = f"These operators aren't allowed for this exercise: {used}."
    return f"{message} Allowed here: {allowed}." if allowed else message


def _attempt_history(exercise, student):
    return Attempt.objects.filter(exercise=exercise, student=student).order_by("-created_at")


def record_attempt(request, **fields):
    """Persist an Attempt, unless a teacher is previewing the student view.

    In preview the submission is still graded and feedback shown, but nothing is
    recorded — keeps class analytics, streaks, and unlock state clean.
    """
    if getattr(request, "is_previewing", False):
        return None
    return Attempt.objects.create(**fields)


@supabase_login_required
@require_POST
def submit_formula(request, exercise_id):
    """Grade a submission by model-checking it against the exercise's graph.

    Same engine path as the sandbox: run_ltl_check(graph, formula) → satisfied
    means correct. Renders the shared sandbox/result.html fragment (real
    counterexample trace included) rather than a fabricated one.
    """
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    student = request.profile

    formula = request.POST.get('formula', '').strip()
    if not formula:
        return _rejected(request, "Enter a formula to check.")
    if len(formula) > MAX_FORMULA_CHARS:
        return _rejected(
            request, f"Formula is too long — at most {MAX_FORMULA_CHARS} characters."
        )

    graph = exercise.kripke_structure
    if not graph:
        return _rejected(request, "This exercise has no model to check against.")

    if exercise.allowed_operators is not None:
        bad = disallowed_operators(formula, exercise.allowed_operators)
        if bad:
            return _rejected(request, _operator_rejection(exercise, bad))

    try:
        result = run_ltl_check(graph, formula)
    except ValueError as exc:
        return _rejected(request, str(exc))
    except Exception:
        logger.exception("run_ltl_check failed during exercise submission")
        return _rejected(
            request, "Verification was stopped — the formula or graph could not be processed."
        )

    is_correct = result["result"] == "satisfied"

    hints_used = _clamped_hints(request, exercise.hints)

    record_attempt(request,
        exercise=exercise,
        student=student,
        formula_input=formula,
        is_correct=is_correct,
        hints_used=hints_used,
    )

    context = build_result_context(result, json.dumps(result["kripke_graph"]))
    response = render(request, "sandbox/result.html", context)
    response.write(render_to_string(
        "exercises/student/_partials/attempts.html",
        {"attempts": _attempt_history(exercise, student), "oob": True},
        request=request,
    ))
    graded_trigger(response, is_correct)
    if is_correct:
        add_trigger(response, "exerciseSolved")
    return response


def _grade_constraint(graph, formula):
    """Grade one required formula against the student's graph → (holds, message, trace).

    An engine ValueError is the model failing the requirement, not a system
    fault — every message it raises is already student-actionable ("references
    proposition(s) not declared on any state", "no initial state", a deadlock),
    so it is surfaced verbatim rather than replaced with a guess at the cause.
    """
    try:
        result = run_ltl_check(graph, formula)
    except ValueError as exc:
        return False, str(exc), None
    if result["result"] == "satisfied":
        return True, "", None
    return False, "Your model has a path that violates this requirement.", result.get("trace")


@supabase_login_required
@require_POST
def submit_kripke(request, exercise_id):
    """Model-check every required formula against the student's graph.

    Correct := all requirements hold on the one submitted model (M ⊨A φ).
    Records a single whole-exercise attempt so completion means one structure
    satisfied everything, never a piecemeal mix of models.
    """
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    if exercise.exercise_type != "build_kripke":
        return _rejected(request, "This exercise is not a build-a-model task.")

    try:
        graph = json.loads(request.POST.get("graph_data") or "")
    except json.JSONDecodeError:
        return _rejected(request, "The Kripke structure could not be read.")
    elements = graph.get("elements") if isinstance(graph, dict) else None
    if not isinstance(elements, dict) or not elements.get("nodes"):
        return _rejected(request, "Draw a Kripke structure before checking.")

    # the only path that hands a student-drawn graph to SPOT — run_ltl_check
    # caps the formula but not the graph, so the sandbox's node cap applies here
    real_nodes = [
        n for n in elements["nodes"] if not (n.get("data") or {}).get("phantom")
    ]
    if len(real_nodes) > MAX_NODES:
        return _rejected(
            request,
            f"Your model has {len(real_nodes)} states — at most {MAX_NODES} are supported.",
        )

    parts = list(exercise.parts.all())
    results = []
    all_ok = bool(parts)
    try:
        for i, part in enumerate(parts, start=1):
            holds, message, trace = _grade_constraint(graph, part.formula)
            all_ok = all_ok and holds
            results.append({
                "number": i, "formula": part.formula,
                "ok": holds, "message": message, "trace": trace,
            })
    except Exception:
        logger.exception("run_ltl_check failed during build_kripke submission")
        return _rejected(
            request, "Verification was stopped — the model could not be processed."
        )

    # the editor posts the whole cy.json() — stylesheet, zoom and pan included —
    # and none of it is read back, so only the elements are stored per attempt
    stored_graph = {
        "elements": {
            "nodes": elements.get("nodes") or [],
            "edges": elements.get("edges") or [],
        }
    }
    record_attempt(request,
        exercise=exercise,
        student=request.profile,
        answer={"graph": stored_graph},
        is_correct=all_ok,
        hints_used=_clamped_hints(
            request, [h for p in parts for h in (p.hints or [])]
        ),
    )

    response = render(request, "exercises/student/_partials/kripke.html", {
        "results": results, "all_ok": all_ok,
    })
    graded_trigger(response, all_ok)
    return _completion_trigger(response, request, exercise)


@supabase_login_required
@require_POST
def submit_buchi(request, exercise_id):
    """Grade a drawn Büchi automaton against the target LTL by language equivalence.

    Records one whole-exercise attempt (buchi_construct has no parts). A drawing
    problem the client validation missed (unlabelled edge, no initial state,
    off-alphabet label) is surfaced to the student, not a 500.
    """
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    if exercise.exercise_type != "buchi_construct":
        return _rejected(request, "This exercise is not a draw-an-automaton task.")

    try:
        automaton = json.loads(request.POST.get("automaton_data") or "")
    except json.JSONDecodeError:
        return _rejected(request, "The automaton could not be read.")
    elements = automaton.get("elements") if isinstance(automaton, dict) else None
    if not isinstance(elements, dict) or not elements.get("nodes"):
        return _rejected(request, "Draw an automaton before checking.")

    symbols = list(exercise.declared_aps or [])
    determinism_answer = (request.POST.get("determinism") or "").strip()
    if exercise.ask_determinism and determinism_answer not in ("deterministic", "nondeterministic"):
        return _rejected(
            request, "Also answer whether your automaton is deterministic."
        )

    try:
        result = run_buchi_equivalence_check(automaton, exercise.target_formula, symbols)
        # graded against the student's OWN drawing (MCL5 p.19 asks whether the
        # automata *they* drew are deterministic), not against the target
        actually_deterministic = (
            run_buchi_determinism_check(automaton, symbols)["deterministic"]
            if exercise.ask_determinism else None
        )
    except ValueError as exc:
        return _rejected(request, str(exc))
    except Exception:
        logger.exception("run_buchi_equivalence_check failed during submission")
        return _rejected(
            request, "Verification was stopped — the automaton could not be processed."
        )

    equivalent = result["equivalent"]
    determinism_ok = True
    if exercise.ask_determinism:
        determinism_ok = (
            determinism_answer == "deterministic"
        ) == actually_deterministic

    answer = {"automaton": automaton}
    if exercise.ask_determinism:
        answer["determinism"] = determinism_answer
    record_attempt(request,
        exercise=exercise,
        student=request.profile,
        answer=answer,
        is_correct=equivalent and determinism_ok,
        hints_used=_clamped_hints(request, exercise.hints),
    )

    # the target formula is the hidden answer key — never rendered to students
    response = render(request, "exercises/student/_partials/buchi.html", {
        "equivalent": equivalent,
        "ask_determinism": exercise.ask_determinism,
        "determinism_ok": determinism_ok,
        "actually_deterministic": actually_deterministic,
    })
    graded_trigger(response, equivalent and determinism_ok)
    return _completion_trigger(response, request, exercise)


@supabase_login_required
@require_POST
def submit_buchi_word(request, exercise_id):
    """Grade a typed lasso word against the exercise's fixed Büchi automaton.

    Records one whole-exercise attempt (buchi_word has no parts). An unreadable
    or off-alphabet word is student data — it comes back as a rejection with a
    message, not an error page.
    """
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    if exercise.exercise_type != "buchi_word":
        return _rejected(request, "This exercise is not an accepting-word task.")

    word = (request.POST.get("word") or "").strip()
    if len(word) > MAX_FORMULA_CHARS:
        return _rejected(
            request, f"Word is too long — at most {MAX_FORMULA_CHARS} characters."
        )

    try:
        result = run_buchi_word_check(
            exercise.kripke_structure, word, list(exercise.declared_aps or [])
        )
    except ValueError as exc:
        # the automaton is teacher data — a bad one is not the student's fault
        logger.warning("buchi_word automaton rejected: %s", exc)
        return _rejected(request, "This exercise's automaton could not be read.")
    except Exception:
        logger.exception("run_buchi_word_check failed during submission")
        return _rejected(
            request, "Verification was stopped — the word could not be processed."
        )

    accepted = result["accepted"]
    record_attempt(request,
        exercise=exercise,
        student=request.profile,
        answer={"word": word},
        is_correct=accepted,
        hints_used=_clamped_hints(request, exercise.hints),
    )

    response = render(request, "exercises/student/_partials/buchi_word.html", {
        "accepted": accepted, "word_error": result["word_error"], "word": word,
    })
    graded_trigger(response, accepted)
    return _completion_trigger(response, request, exercise)


def _part_result(request, part, status, message):
    response = render(request, "exercises/student/_partials/part.html", {
        "part": part,
        "status": status,
        "message": message,
    })
    graded_trigger(response, status == "correct")
    return response


def _parse_trace_field(request, name):
    try:
        value = json.loads(request.POST.get(name) or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        return None
    return value


def _submit_path_part(request, exercise, part):
    prefix = _parse_trace_field(request, "trace_prefix")
    cycle = _parse_trace_field(request, "trace_cycle")
    if prefix is None or cycle is None:
        return _part_result(request, part, "error", "Select a path before submitting.")
    if not cycle:
        return _part_result(
            request, part, "error",
            "Close the loop first — click a state already on your path to form the cycle.",
        )
    if not exercise.kripke_structure:
        return _part_result(request, part, "error", "This exercise has no model to check against.")

    try:
        result = run_trace_check(exercise.kripke_structure, part.formula, prefix, cycle)
    except ValueError as exc:
        return _part_result(request, part, "error", str(exc))
    except Exception:
        logger.exception("run_trace_check failed during part submission")
        return _part_result(
            request, part, "error",
            "Verification was stopped — the path could not be processed.",
        )

    is_correct = bool(result["path_ok"] and result["holds"])
    record_attempt(request,
        exercise=exercise,
        student=request.profile,
        part=part,
        answer={"prefix": prefix, "cycle": cycle},
        is_correct=is_correct,
        hints_used=_clamped_hints(request, part.hints),
    )

    if is_correct:
        response = _part_result(
            request, part, "correct",
            "Correct — your path satisfies the formula.",
        )
        return _completion_trigger(response, request, exercise)
    if not result["path_ok"]:
        return _part_result(request, part, "incorrect", result["path_error"])
    return _part_result(
        request, part, "incorrect",
        "That is a valid path of the model, but the formula does not hold on it.",
    )


def _judge_holds(exercise, part):
    """Whether part.formula holds on the model. Uses the answer cached at save;
    falls back to a live check only if it was never computed."""
    if part.answer_holds is not None:
        return part.answer_holds
    return run_ltl_check(exercise.kripke_structure, part.formula)["result"] == "satisfied"


def _submit_judge_part(request, exercise, part):
    verdict = request.POST.get("verdict", "").strip()
    if verdict not in ("holds", "violated"):
        return _part_result(request, part, "error", "Choose a verdict first.")
    if not exercise.kripke_structure:
        return _part_result(request, part, "error", "This exercise has no model to check against.")

    if verdict == "holds":
        try:
            actually_holds = _judge_holds(exercise, part)
        except ValueError as exc:
            return _part_result(request, part, "error", str(exc))
        except Exception:
            logger.exception("judge truth check failed during submission")
            return _part_result(
                request, part, "error",
                "Verification was stopped — the formula could not be processed.",
            )
        answer = {"verdict": "holds"}
        is_correct = actually_holds
        message = (
            "Correct — the formula holds on every path of the model."
            if is_correct
            else "The formula does not hold universally — there is a path that violates it."
        )
    else:
        prefix = _parse_trace_field(request, "trace_prefix")
        cycle = _parse_trace_field(request, "trace_cycle")
        if prefix is None or cycle is None:
            return _part_result(
                request, part, "error",
                "Select a counterexample path before submitting.",
            )
        if not cycle:
            return _part_result(
                request, part, "error",
                "Close the loop first — click a state already on your path to form the cycle.",
            )
        try:
            actually_holds = _judge_holds(exercise, part)
            trace = run_trace_check(exercise.kripke_structure, part.formula, prefix, cycle)
        except ValueError as exc:
            return _part_result(request, part, "error", str(exc))
        except Exception:
            logger.exception("judge counterexample check failed during submission")
            return _part_result(
                request, part, "error",
                "Verification was stopped — the path could not be processed.",
            )
        answer = {"verdict": "violated", "prefix": prefix, "cycle": cycle}
        is_correct = (
            not actually_holds and trace["path_ok"] and trace["holds"] is False
        )
        if is_correct:
            message = "Correct — the formula does not hold, and your path witnesses the violation."
        elif actually_holds:
            message = "The formula actually holds on every path — no counterexample exists."
        elif not trace["path_ok"]:
            message = trace["path_error"]
        else:
            message = "The formula holds on your chosen path — find a path where it fails."

    record_attempt(request,
        exercise=exercise,
        student=request.profile,
        part=part,
        answer=answer,
        is_correct=is_correct,
        hints_used=_clamped_hints(request, part.hints),
    )

    response = _part_result(
        request, part, "correct" if is_correct else "incorrect", message
    )
    if is_correct:
        return _completion_trigger(response, request, exercise)
    return response


@supabase_login_required
@require_POST
def submit_part(request, exercise_id, part_id):
    """Grade a sub-question submission and render its result partial."""
    exercise = get_object_or_404(published_exercises(), id=exercise_id)
    part = get_object_or_404(ExercisePart, pk=part_id, exercise=exercise)

    if exercise.exercise_type == "path_exhibit":
        return _submit_path_part(request, exercise, part)
    if exercise.exercise_type == "judge":
        return _submit_judge_part(request, exercise, part)
    if exercise.exercise_type != "english_to_formula":
        return _part_result(request, part, "error", "This exercise type is not submittable yet.")

    formula = request.POST.get("formula", "").strip()
    if not formula:
        return _part_result(request, part, "error", "Enter a formula to check.")
    if len(formula) > MAX_FORMULA_CHARS:
        return _part_result(
            request, part, "error",
            f"Formula is too long — at most {MAX_FORMULA_CHARS} characters.",
        )

    if exercise.allowed_operators is not None:
        bad = disallowed_operators(formula, exercise.allowed_operators)
        if bad:
            return _part_result(request, part, "error", _operator_rejection(exercise, bad))

    try:
        result = run_equivalence_check(part.formula, formula, exercise.declared_aps or [])
    except ValueError as exc:
        return _part_result(request, part, "error", str(exc))
    except Exception:
        logger.exception("run_equivalence_check failed during part submission")
        return _part_result(
            request, part, "error",
            "Verification was stopped — the formula could not be processed.",
        )

    is_correct = result["equivalent"]

    record_attempt(request,
        exercise=exercise,
        student=request.profile,
        part=part,
        formula_input=formula,
        is_correct=is_correct,
        hints_used=_clamped_hints(request, part.hints),
    )

    if is_correct:
        response = _part_result(
            request, part, "correct",
            "Correct — your formula is equivalent to the requirement.",
        )
        return _completion_trigger(response, request, exercise)
    return _part_result(
        request, part, "incorrect",
        "Not equivalent to the requirement — check which behaviours your formula allows or forbids.",
    )
