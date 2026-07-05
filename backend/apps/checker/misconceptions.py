"""Classify a wrong LTL submission against its target using the SPOT engine.

Deterministic and semantic: instead of matching substrings in the formula text,
ask SPOT for the real relationship (equivalent / negation / implication) between
the student's formula S and the target T, then name the specific operator slip by
applying one *single-position* edit to S and checking whether it becomes
equivalent to T. No heuristics.

Returns a bucket key (see ``BUCKETS``) or ``None`` when there is no misconception
to report (S is equivalent to T, or the formulas cannot be parsed/compared).
"""
from functools import lru_cache

from .engine import _SPOT_AVAILABLE, _normalize_formula

try:
    import spot  # type: ignore[import]
except ImportError:  # pragma: no cover - mirrors engine's optional import
    spot = None

BUCKETS = (
    "g_vs_f",
    "f_vs_x",
    "missing_global",
    "missing_eventually",
    "inverted",
    "mistranslation",
)

# Priority when more than one single-position edit reconciles S with T.
_EDIT_ORDER = ("f_vs_x", "g_vs_f", "missing_global", "missing_eventually")

# Operator kinds the rewrite search can safely reconstruct (LTL + boolean fragment).
# SERE / rational operators (Concat, Fusion, Star, ...) must be excluded: rebuilding
# one via binop/unop aborts SPOT natively (SIGILL) — an uncatchable crash that would
# take down the worker. are_equivalent/Not handle them fine, so only the rewrite is gated.
_SUPPORTED_KINDS = frozenset({
    spot.op_tt, spot.op_ff, spot.op_ap,
    spot.op_Not, spot.op_X, spot.op_F, spot.op_G,
    spot.op_U, spot.op_R, spot.op_W, spot.op_M,
    spot.op_And, spot.op_Or, spot.op_Implies, spot.op_Xor, spot.op_Equiv,
}) if spot is not None else frozenset()


def _supported(f):
    """True iff every node in ``f`` is an LTL/boolean kind the rewrite can rebuild."""
    if f.kind() not in _SUPPORTED_KINDS:
        return False
    return all(_supported(f[i]) for i in range(f.size()))


def _rebuild(f, kids):
    """Reconstruct node ``f`` with new children, dispatching by operator category.

    And/Or are variadic multops even with two children, so an arity-based rebuild
    segfaults SPOT — hence the explicit multop check.
    """
    kind = f.kind()
    if len(kids) == 1:
        return spot.formula.unop(kind, kids[0])
    if kind in (spot.op_And, spot.op_Or):
        return spot.formula.multop(kind, kids)
    return spot.formula.binop(kind, kids[0], kids[1])


def _local_edits(f):
    """Single-operator edits applied AT node ``f`` (not its descendants)."""
    kind = f.kind()
    if kind == spot.op_X:
        yield ("f_vs_x", spot.formula.F(f[0]))
    elif kind == spot.op_F:
        yield ("f_vs_x", spot.formula.X(f[0]))
        yield ("g_vs_f", spot.formula.G(f[0]))
    elif kind == spot.op_G:
        yield ("g_vs_f", spot.formula.F(f[0]))
    # a G/F the student dropped, at this position (top-level or around a subterm)
    yield ("missing_global", spot.formula.G(f))
    yield ("missing_eventually", spot.formula.F(f))


def _single_edits(f):
    """Yield (bucket, formula) for every single-position edit anywhere in ``f``."""
    yield from _local_edits(f)
    n = f.size()
    for i in range(n):
        for bucket, new_child in _single_edits(f[i]):
            kids = [f[j] for j in range(n)]
            kids[i] = new_child
            yield (bucket, _rebuild(f, kids))


def _swap_all(f, a, b):
    """Rebuild ``f`` swapping every op of kind ``a`` with ``b`` (and vice versa)."""
    if f.size() == 0:
        return f
    kids = [_swap_all(f[i], a, b) for i in range(f.size())]
    kind = f.kind()
    if kind == a:
        kind = b
    elif kind == b:
        kind = a
    if f.size() == 1:
        return spot.formula.unop(kind, kids[0])
    if kind in (spot.op_And, spot.op_Or):
        return spot.formula.multop(kind, kids)
    return spot.formula.binop(kind, kids[0], kids[1])


def _slip_candidates(s):
    """All rewrite candidates: single-position edits, plus the systematic
    swap-everywhere of X<->F and F<->G (catches GF-vs-FG and consistent confusions)."""
    candidates = list(_single_edits(s))
    candidates.append(("f_vs_x", _swap_all(s, spot.op_X, spot.op_F)))
    candidates.append(("g_vs_f", _swap_all(s, spot.op_F, spot.op_G)))
    return candidates


@lru_cache(maxsize=2048)
def classify_misconception(target, submitted):
    if not _SPOT_AVAILABLE or spot is None:
        return None
    if not submitted or not target:
        return None
    try:
        t = spot.formula(_normalize_formula(target))
        s = spot.formula(_normalize_formula(submitted))
    except Exception:
        # Unparseable input (syntax error, or a malformed target) is not an LTL
        # misconception — exclude it rather than mislabel a translation error.
        return None

    if spot.are_equivalent(s, t):
        return None
    if spot.are_equivalent(s, spot.formula.Not(t)):
        return "inverted"

    # Name the specific operator slip: does one single-position edit of S yield T?
    # Highest-priority bucket wins; stop at the first equivalence match. Only the
    # LTL/boolean fragment can be safely rewritten (see _supported).
    if _supported(s):
        candidates = _slip_candidates(s)
        for want in _EDIT_ORDER:
            for bucket, variant in candidates:
                if bucket == want and spot.are_equivalent(variant, t):
                    return want

    # No single operator slip explains it (strictly stronger/weaker, or unrelated)
    # — an English-to-LTL authoring error.
    return "mistranslation"
