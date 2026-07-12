"""Operator-restriction check for student submissions.

A teacher may limit which LTL operators an exercise permits. This inspects a
submitted formula's operators (via SPOT, reusing the engine's normaliser) and
reports any that the exercise does not allow. It only reads the parsed formula;
it does not model-check and does not modify the engine.
"""

from .engine import _SPOT_AVAILABLE, _normalize_formula

try:
    import spot
except ImportError:  # pragma: no cover - matches engine's optional-SPOT guard
    spot = None

# SPOT operator kindstr → the builder operator symbol it corresponds to.
_KIND_TO_SYMBOL = {
    "G": "G", "F": "F", "X": "X", "U": "U",
    "Not": "¬", "And": "∧", "Or": "∨", "Implies": "→",
}

# operator kinds a teacher can never enable (no builder button) — always rejected
_UNSUPPORTED_LABELS = {
    "R": "R (release)", "W": "W (weak until)", "M": "M (strong release)",
    "Equiv": "↔ (equivalence)", "Xor": "xor",
}

# leaves — not operators
_NON_OPERATOR = {"ap", "tt", "ff"}


def disallowed_operators(formula_str, allowed_symbols):
    """Operators in the formula the exercise does not permit.

    Returns a set of tokens: builder symbols (G/F/X/U/¬/∧/∨/→) for disallowed
    builder operators, and a descriptive label for operators with no builder
    equivalent (R/W/M/↔/xor). The caller labels the symbols for display. Empty
    when all operators are allowed, or — deferring to run_ltl_check — when SPOT
    is unavailable or the formula does not parse.
    ``allowed_symbols`` is the exercise's allowed-operator list.
    """
    if not _SPOT_AVAILABLE or spot is None:
        return set()
    allowed = set(allowed_symbols or [])
    bad = set()

    def walk(node):
        kind = node.kindstr()
        if kind not in _NON_OPERATOR:
            symbol = _KIND_TO_SYMBOL.get(kind)
            if symbol is None:
                bad.add(_UNSUPPORTED_LABELS.get(kind, kind))
            elif symbol not in allowed:
                bad.add(symbol)
        for child in node:
            walk(child)

    try:
        walk(spot.formula(_normalize_formula(formula_str)))
    except (SyntaxError, RuntimeError, ValueError, RecursionError):
        return set()
    return bad
