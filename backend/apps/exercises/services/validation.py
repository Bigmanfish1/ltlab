import json

from apps.checker.engine import validate_request
from apps.checker.equivalence import validate_formula_submission
from apps.checker.operators import disallowed_operators
from apps.checker.tasks import (
    run_buchi_shown_automaton_check,
    run_buchi_target_check,
    run_ltl_check,
    run_model_solvable_check,
)
from apps.checker.views import _PROP_NAME_RE, _RESERVED_PROP_NAMES

from ..constants import DIFFICULTIES, operator_label
from .common import BUILDER_EXERCISE_TYPES, _effective_type, _topic_exists


def _validate_declared_aps(declared_aps, errors, noun="atomic proposition"):
    if not declared_aps:
        errors.append(f"Declare at least one {noun}.")
    for ap in declared_aps:
        if not _PROP_NAME_RE.match(ap):
            errors.append(f"'{ap}' is not a valid {noun} name.")
        elif ap in _RESERVED_PROP_NAMES:
            errors.append(f"'{ap}' is a reserved LTL keyword.")


def _validate_buchi_construct(form, errors):
    """Require an alphabet Σ and a target LTL over it with a non-empty language.

    Σ symbols are exclusive (one per word step), so a formula can be satisfiable
    over 2^AP yet accept nothing over Σ — that would be unsolvable, so it is
    rejected here rather than left for students to fail against.
    """
    _validate_declared_aps(form["declared_aps"], errors, noun="alphabet symbol")
    target = form["target_formula"]
    if not target:
        errors.append("Enter the target LTL formula the automaton must accept.")
        return
    if errors:
        return
    try:
        result = run_buchi_target_check(target, form["declared_aps"])
    except ValueError as exc:
        errors.append(f"Target formula: {exc}")
        return
    if result["empty"]:
        errors.append(
            f"No word over the alphabet satisfies {target}, so no Büchi automaton "
            "could accept it — students could never solve the exercise."
        )


def _check_operators(formula, allowed_operators, label, errors):
    """Flag a teacher-authored formula that uses operators students can't enter."""
    bad = disallowed_operators(formula, allowed_operators)
    if bad:
        labels = ", ".join(sorted(operator_label(t) for t in bad))
        errors.append(
            f"{label} uses operators students can't enter: {labels}. "
            "Enable them under Allowed Operators or rewrite the formula."
        )


def _validate_english_parts(form, errors):
    if not form["parts"]:
        errors.append("Add at least one requirement with a target formula.")
    for i, part in enumerate(form["parts"], start=1):
        if not part["prompt"]:
            errors.append(f"Requirement {i} needs its English prompt.")
        if not part["formula"]:
            errors.append(f"Requirement {i} needs a target formula.")
            continue
        try:
            validate_formula_submission(part["formula"], form["declared_aps"])
        except ValueError as exc:
            errors.append(f"Requirement {i} target: {exc}")
            continue
        # a target using an operator students can't enter is unsolvable —
        # no permitted formula could ever be equivalent to it
        _check_operators(part["formula"], form["allowed_operators"], f"Requirement {i} target", errors)


def _validate_judge_parts(form, graph, errors):
    if not form["parts"]:
        errors.append("Add at least one formula for students to judge.")
        return
    for i, part in enumerate(form["parts"], start=1):
        if not part["formula"]:
            errors.append(f"Formula {i} is empty.")
            continue
        try:
            validate_request(graph, part["formula"])
        except ValueError as exc:
            errors.append(f"Formula {i}: {exc}")
            continue
        _check_operators(part["formula"], form["allowed_operators"], f"Formula {i}", errors)


def judge_answer_key(exercise):
    """(position, formula, holds) per part — the teacher-facing answer key.

    Reads the answer cached at save (_store_judge_answers), which is recomputed
    on every edit, so it never drifts from the graph."""
    return [
        (i, part.formula, bool(part.answer_holds))
        for i, part in enumerate(exercise.parts.all(), start=1)
    ]


def formula_satisfiable(graph, formula):
    """∃ path of the model satisfying φ — a counterexample to !(φ) is exactly
    a satisfying path for φ. Shared by the publish gate and the builder's
    Test button so the two can never disagree."""
    return run_ltl_check(graph, f"!({formula})")["result"] == "violated"


def _validate_path_parts(form, graph, errors):
    """Each formula must parse against the graph AND have a satisfying lasso —
    an unsatisfiable formula would make the part impossible for students."""
    if not form["parts"]:
        errors.append("Add at least one formula for students to find a path for.")
        return
    for i, part in enumerate(form["parts"], start=1):
        if not part["formula"]:
            errors.append(f"Formula {i} is empty.")
            continue
        try:
            satisfiable = formula_satisfiable(graph, part["formula"])
        except ValueError as exc:
            errors.append(f"Formula {i}: {exc}")
            continue
        if not satisfiable:
            errors.append(
                f"Formula {i} ({part['formula']}) has no satisfying path on this "
                "structure — students could never solve it."
            )
            continue
        _check_operators(part["formula"], form["allowed_operators"], f"Formula {i}", errors)


def _validate_build_kripke_parts(form, errors):
    """Require ≥1 formula, all over the declared APs and jointly satisfiable."""
    if not form["parts"]:
        errors.append("Add at least one formula the student's model must satisfy.")
        return
    formulas = []
    for i, part in enumerate(form["parts"], start=1):
        if not part["formula"]:
            errors.append(f"Formula {i} is empty.")
            continue
        formulas.append(part["formula"])
    if len(formulas) != len(form["parts"]):
        return
    try:
        solvable = run_model_solvable_check(formulas, form["declared_aps"])["solvable"]
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not solvable:
        errors.append(
            "These requirements contradict each other — no Kripke structure can "
            "satisfy them all, so students could never solve the exercise."
        )


def _validate_buchi_word(form, graph, errors):
    """Require an alphabet and a shown automaton that actually accepts something."""
    _validate_declared_aps(form["declared_aps"], errors, noun="alphabet symbol")
    if not graph:
        errors.append("Draw the Büchi automaton students will read.")
    if errors:
        return
    try:
        empty = run_buchi_shown_automaton_check(graph, form["declared_aps"])["empty"]
    except ValueError as exc:
        errors.append(f"Automaton: {exc}")
        return
    if empty:
        errors.append(
            "This automaton accepts no words at all, so students could never "
            "give an accepting word."
        )


def validate_exercise_form(form, exercise, publishing):
    errors = []
    if not form["title"]:
        errors.append("Exercise title is required.")
    if not form["description"]:
        errors.append("Task description is required.")
    if form["difficulty"] not in DIFFICULTIES:
        errors.append("Select a difficulty.")
    if not _topic_exists(form["module_id"]):
        errors.append("Assign the exercise to a module.")

    exercise_type = _effective_type(form, exercise)
    if exercise_type not in BUILDER_EXERCISE_TYPES:
        errors.append("Unknown exercise type.")
        return errors, None

    # buchi_word's drawing is the exercise's automaton, posted on its own field
    is_automaton = exercise_type == "buchi_word"
    raw_graph = form["automaton_data"] if is_automaton else form["graph_data"]
    graph = None
    if raw_graph:
        try:
            graph = json.loads(raw_graph)
        except json.JSONDecodeError:
            errors.append(
                "The automaton could not be read." if is_automaton
                else "The Kripke structure could not be read."
            )
    elif exercise is not None:
        graph = exercise.kripke_structure

    if publishing and not errors:
        if exercise_type in ("model_check", "english_to_formula") and not form["allowed_operators"]:
            errors.append(
                "No operators are enabled — students could only submit a bare atomic "
                "proposition. Enable the operators this exercise needs."
            )
        if exercise_type == "english_to_formula":
            _validate_declared_aps(form["declared_aps"], errors)
            _validate_english_parts(form, errors)
        elif exercise_type == "build_kripke":
            # student supplies the graph; validate the required formulas instead
            _validate_declared_aps(form["declared_aps"], errors)
            _validate_build_kripke_parts(form, errors)
        elif exercise_type == "buchi_construct":
            # student draws the automaton; validate the alphabet and target
            _validate_buchi_construct(form, errors)
        elif exercise_type == "buchi_word":
            # teacher supplies the automaton; students supply a word for it
            _validate_buchi_word(form, graph, errors)
        elif not graph:
            # Students are graded against this graph (model-checking their
            # formula, or walking their path on it), so publishing needs one.
            errors.append("Publishing needs a memorandum Kripke structure.")
        elif exercise_type == "path_exhibit":
            _validate_path_parts(form, graph, errors)
        elif exercise_type == "judge":
            _validate_judge_parts(form, graph, errors)
    return errors, graph
