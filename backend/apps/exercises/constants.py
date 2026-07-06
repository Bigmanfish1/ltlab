"""Presentation constants for the exercises app.

Human-readable labels/descriptions for the misconception buckets produced by
apps.checker.misconceptions.classify_misconception. Keys must match that
module's BUCKETS.
"""

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
