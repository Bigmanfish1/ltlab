"""Formula-equivalence grading for english_to_formula exercises.

No Kripke structure is involved: the student's formula is graded against the
exercise part's hidden target via SPOT language equivalence (automata-based,
see spot.are_equivalent). Validation reuses the engine's structural caps, but
the AP-subset check runs against the exercise's declared AP list instead of a
graph's state labels.
"""

from .engine import (
    MAX_FORMULA_APS,
    MAX_FORMULA_NODES,
    MAX_TEMPORAL_OPS,
    _collect_formula_aps_set,
    _count_temporal_ops,
    _formula_node_count,
    _normalize_formula,
    _op_constants,
    _require_spot,
)

try:
    import spot  # type: ignore[import]
except ImportError:  # pragma: no cover - mirrors engine's optional import
    spot = None


def _parse(formula_str: str):
    try:
        return spot.formula(_normalize_formula(formula_str))
    except (SyntaxError, RuntimeError) as exc:
        raise ValueError(f"Invalid LTL formula: {exc}") from exc


def validate_formula_submission(formula_str: str, declared_aps: list[str]) -> None:
    """Pre-flight a formula against the caps and a declared AP list.

    Raises ValueError with a user-facing message on any violation.
    """
    _require_spot()
    f = _parse(formula_str)
    ops = _op_constants()

    node_count = _formula_node_count(f)
    if node_count > MAX_FORMULA_NODES:
        raise ValueError(
            f"Formula is too complex ({node_count} subformulas) — "
            f"at most {MAX_FORMULA_NODES} are supported."
        )

    temporal = _count_temporal_ops(f, ops)
    if temporal > MAX_TEMPORAL_OPS:
        raise ValueError(
            f"Formula contains {temporal} temporal operators (X/F/G/U/R/W/M) — "
            f"at most {MAX_TEMPORAL_OPS} are supported."
        )

    formula_aps = _collect_formula_aps_set(f, ops)
    if len(formula_aps) > MAX_FORMULA_APS:
        raise ValueError(
            f"Formula references {len(formula_aps)} distinct propositions — "
            f"at most {MAX_FORMULA_APS} are supported."
        )

    undeclared = formula_aps - set(declared_aps)
    if undeclared:
        names = ", ".join(sorted(undeclared))
        raise ValueError(
            f"Formula references proposition(s) not in this exercise: {names}. "
            "Use only the listed atomic propositions."
        )


def check_equivalence(target_str: str, submitted_str: str) -> bool:
    """True iff the two formulas define the same language (spot.are_equivalent)."""
    _require_spot()
    return spot.are_equivalent(_parse(target_str), _parse(submitted_str))


def formulas_jointly_satisfiable(formulas: list[str], declared_aps: list[str]) -> bool:
    """True iff some model satisfies every formula at once (their conjunction).

    A satisfiable conjunction guarantees a build-a-Kripke exercise is solvable:
    any word satisfying it yields a lasso Kripke structure M with M ⊨A each.
    """
    _require_spot()
    for formula_str in formulas:
        validate_formula_submission(formula_str, declared_aps)
    conjunction = spot.formula.And([_parse(f) for f in formulas])
    return not spot.translate(conjunction).is_empty()
