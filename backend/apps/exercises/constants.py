"""Presentation constants for the exercises app.

Human-readable labels/descriptions for the misconception buckets produced by
apps.checker.misconceptions.classify_misconception. Keys must match that
module's BUCKETS.
"""

BUILDER_OPERATORS = ["G", "F", "X", "U", "¬", "∧", "∨", "→"]
OPERATOR_LABELS = {
    "G": "Always", "F": "Eventually", "X": "Next", "U": "Until",
    "¬": "Not", "∧": "And", "∨": "Or", "→": "Implies",
}
# ∨ and | are the same operator; both glyphs are offered wherever ∨ appears
OPERATOR_DISPLAY = {"∨": "∨ / |"}
DIFFICULTIES = ["beginner", "intermediate", "advanced"]

# badge text per exercise type — compact forms of the builder's type labels
# (module terminology: MCL3 p.2/3 "formalising", p.27 universal/existential MC)
EXERCISE_TYPE_BADGES = {
    "model_check": "Formalise a Property",
    "english_to_formula": "Requirements → LTL",
    "path_exhibit": "∃ Model Checking",
    "judge": "∀ Model Checking",
}

MISCONCEPTION_LABELS = {
    "g_vs_f": "G vs F confusion",
    "f_vs_x": "F vs X confusion",
    "missing_global": "Missing G (always)",
    "missing_eventually": "Missing F (eventually)",
    "inverted": "Inverted property",
    "mistranslation": "English to LTL translation",
}

MISCONCEPTION_DESCRIPTIONS = {
    "g_vs_f": "swapped G (always) and F (eventually) — e.g. FG vs GF, or always vs eventually",
    "f_vs_x": "used X (next) where F (eventually) was required, or vice versa",
    "missing_global": "omitted the G (always) that the specification requires",
    "missing_eventually": "omitted the F (eventually) that the specification requires",
    "inverted": "expressed the negation of the target property",
    "mistranslation": "mistranslated the plain-English requirement into LTL",
}
