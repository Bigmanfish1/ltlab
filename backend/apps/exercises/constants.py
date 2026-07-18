"""Presentation constants for the exercises app."""

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
    "build_kripke": "Model a Property",
    "buchi_construct": "Draw a Büchi Automaton",
    "buchi_word": "Give an Accepting Word",
}

# sample labels for the placeholder misconception panel (analytics reworking)
MISCONCEPTION_LABELS = {
    "g_vs_f": "G vs F confusion",
    "f_vs_x": "F vs X confusion",
    "missing_global": "Missing G (always)",
}
