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
DIFFICULTIES = ["beginner", "intermediate", "advanced"]

# badge text per exercise type — must match the builder's type-selector labels
EXERCISE_TYPE_BADGES = {
    "model_check": "Formula on Graph",
    "english_to_formula": "English → Formula",
    "path_exhibit": "Find a Path",
    "judge": "Judge",
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
