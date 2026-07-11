"""Adversarial red-team of the misconception classifier.

Property-based: for every hand-designed (target, submitted) pair we compute an
INDEPENDENT SPOT ground-truth relation (equivalent / negation / stronger / weaker
/ unrelated / unparseable) and assert the classifier is *consistent* with it —
never a false positive (bucket for equivalent), never a false negative (None for
a differing pair), never a crash. Bucket names themselves are asserted in
test_misconceptions.py; here we guard the semantic invariants over many cases.

Pairs were designed from LTL semantics and known misconceptions independently of
the classifier's implementation (originally an adversarial subagent harness).
"""
from django.test import SimpleTestCase

from apps.checker.engine import _SPOT_AVAILABLE
from apps.checker.misconceptions import classify_misconception

try:
    import spot
except ImportError:  # pragma: no cover
    spot = None

_UNICODE_MAP = {"¬": "!", "∧": "&", "∨": "|", "→": "->", "↔": "<->"}


def _to_ascii(s):
    for k, v in _UNICODE_MAP.items():
        s = s.replace(k, v)
    return s


# (target, submitted) probes. Grouped by the adversarial intent in comments.
ADVERSARIAL_PAIRS = [
    # operator slips wrapped in context
    ("G(req -> F grant)", "G(req -> X grant)"),
    ("G(a -> F b)", "F(a -> F b)"),
    ("G(a -> X b)", "a -> X b"),
    ("G(req -> F grant)", "G(req -> grant)"),
    ("F X a", "F F a"),
    ("G F a", "G X a"),
    # negation / duality
    ("G a", "F !a"),
    ("G a", "!(G a)"),
    ("G(a & b)", "F(!a | !b)"),
    ("G(a -> F b)", "!(G(a -> F b))"),
    ("F a", "G !a"),
    # equivalent-but-different (must be no misconception)
    ("a & b", "b & a"),
    ("G G a", "G a"),
    ("F a", "true U a"),
    ("a | a", "a"),
    ("a | (a & b)", "a"),
    ("F F a", "F a"),
    ("G(a & b)", "G a & G b"),
    ("X(a & b)", "X a & X b"),
    ("G(req -> F grant)", "G(!req | F grant)"),
    # strictly stronger / weaker, multi-error
    ("F(a | b)", "G a"),
    ("G a", "G(a | b)"),
    ("G(a | b)", "G a | G b"),
    ("G(a & b)", "F a & G b"),
    # until subtleties
    ("a U b", "(a U b) | G a"),
    ("a U b", "F b"),
    ("(a U b) | G a", "a U b"),
    ("a U b", "a & F b"),
    # distribution traps
    ("F(a & b)", "F a & F b"),
    ("G(a | b)", "G a | G b"),
    # unicode / whitespace (semantically correct, except G¬a which is the negation)
    ("G(a -> F b)", "G(a → F b)"),
    ("G ¬(c1 ∧ c2)", "G ¬(c2 ∧ c1)"),
    ("G(a -> F b)", "G (  a  ->  F  b  )"),
    ("G a", "G¬a"),
    # malformed / unparseable
    ("G a", "G G ("),
    ("G a", ""),
    ("G a", "F"),
    ("G a", "asdf qwer"),
    ("G F a", "G F G F G F G F G F G a"),
    # overlap / ambiguity
    ("G F a", "F G a"),
    ("G(a -> F b)", "F(a -> X b)"),
    ("G a", "X a"),
]

_DIFFERING = {"negation", "stronger", "weaker", "unrelated"}


def _ground_truth(target, submitted):
    """Independent SPOT relation, not derived from the classifier."""
    at, asub = _to_ascii(target), _to_ascii(submitted)
    try:
        ft = spot.formula(at)
        fs = spot.formula(asub)
    except Exception:
        return "unparseable"
    if spot.are_equivalent(ft, fs):
        return "equivalent"
    try:
        if spot.are_equivalent(ft, spot.formula("!(" + asub + ")")):
            return "negation"
    except Exception:
        pass
    if spot.contains(ft, fs):   # L(submitted) ⊆ L(target): submitted implies target
        return "stronger"
    if spot.contains(fs, ft):   # L(target) ⊆ L(submitted): target implies submitted
        return "weaker"
    return "unrelated"


class AdversarialClassifierTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _SPOT_AVAILABLE or spot is None:
            raise cls.skipTest(cls, "SPOT not available")

    def test_no_crash_no_false_positive_no_false_negative(self):
        for target, submitted in ADVERSARIAL_PAIRS:
            with self.subTest(target=target, submitted=submitted):
                try:
                    bucket = classify_misconception(target, submitted)
                except Exception as exc:  # noqa: BLE001 - crash is the failure
                    self.fail(f"classifier crashed on {submitted!r} vs {target!r}: {exc}")

                relation = _ground_truth(target, submitted)
                if relation == "equivalent":
                    self.assertIsNone(bucket, f"false positive: {submitted!r} ≡ {target!r}")
                elif relation == "unparseable":
                    self.assertIsNone(bucket, f"unparseable should be excluded: {submitted!r}")
                elif relation == "negation":
                    self.assertEqual(bucket, "inverted", f"{submitted!r} is ¬({target!r})")
                elif relation in _DIFFERING:
                    self.assertIsNotNone(
                        bucket, f"false negative: {submitted!r} differs from {target!r}"
                    )
                    self.assertNotEqual(bucket, "inverted")
