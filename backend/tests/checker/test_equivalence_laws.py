from itertools import combinations

from django.test import SimpleTestCase

from apps.checker.equivalence import check_equivalence, validate_formula_submission
from apps.checker.tasks import run_equivalence_check

PQ = ["p", "q"]
COFFEE_APS = ["request", "coffee"]

POSITIVE_PAIRS = [
    ("F p", "true U p"),
    ("G p", "!F!p"),
    ("!G p", "F !p"),
    ("!F p", "G !p"),
    ("!X p", "X !p"),
    ("G(p & q)", "G p & G q"),
    ("F(p | q)", "F p | F q"),
    ("X(p U q)", "(X p) U (X q)"),
    ("F F p", "F p"),
    ("G G p", "G p"),
    ("p U (p U q)", "p U q"),
    ("F G F p", "G F p"),
    ("G F G p", "F G p"),
    ("p U q", "q | (p & X(p U q))"),
    ("G(p -> q)", "G(!p | q)"),
    ("p ∨ q", "p | q"),
    ("G(p→q)", "G(p -> q)"),
]

NEGATIVE_PAIRS = [
    ("request -> F coffee", "G(request -> F coffee)"),
    ("G(p -> X q)", "G(p -> F q)"),
    ("p U q", "q U p"),
    ("G(p -> q)", "G(q -> p)"),
    ("p W q", "p U q"),
    ("G F p", "F G p"),
    ("G p", "p"),
    ("F(p & q)", "F p & F q"),
    ("G(p | q)", "G p | G q"),
    ("X p", "F p"),
    ("F coffe", "F coffee"),
]


class CheckEquivalenceLawTests(SimpleTestCase):
    def test_positive_pairs_equivalent(self):
        for a, b in POSITIVE_PAIRS:
            with self.subTest(a=a, b=b):
                self.assertTrue(check_equivalence(a, b))

    def test_negative_pairs_not_equivalent(self):
        for a, b in NEGATIVE_PAIRS:
            with self.subTest(a=a, b=b):
                self.assertFalse(check_equivalence(a, b))

    def test_symmetry(self):
        for a, b in [("F p", "true U p"), ("G F p", "F G p"), ("p U q", "q U p")]:
            with self.subTest(a=a, b=b):
                self.assertEqual(check_equivalence(a, b), check_equivalence(b, a))

    def test_glyph_disjunction_matches_ascii(self):
        self.assertTrue(check_equivalence("F(p ∨ q)", "F p | F q"))


class RunEquivalenceCheckLawTests(SimpleTestCase):
    def test_positive_pairs_through_task(self):
        pairs = [
            ("F p", "true U p"),
            ("G p", "!F!p"),
            ("G(p & q)", "G p & G q"),
            ("p U q", "q | (p & X(p U q))"),
            ("G(p -> q)", "G(!p | q)"),
        ]
        for target, submitted in pairs:
            with self.subTest(target=target, submitted=submitted):
                result = run_equivalence_check(target, submitted, PQ)
                self.assertTrue(result["equivalent"])

    def test_coffee_machine_equivalences(self):
        target = "G(request -> F coffee)"
        for submitted in ["G(!request | F coffee)", "!F(request & G !coffee)"]:
            with self.subTest(submitted=submitted):
                result = run_equivalence_check(target, submitted, COFFEE_APS)
                self.assertTrue(result["equivalent"])

    def test_negative_pair_through_task(self):
        result = run_equivalence_check(
            "G(request -> F coffee)", "request -> F coffee", COFFEE_APS
        )
        self.assertFalse(result["equivalent"])

    def test_happy_path_exact_dict_verbatim_formula(self):
        submitted = "G(request → F coffee)"
        result = run_equivalence_check("G(request -> F coffee)", submitted, COFFEE_APS)
        self.assertEqual(result, {"equivalent": True, "formula": submitted})

    def test_unparseable_target_raises(self):
        with self.assertRaises(ValueError):
            run_equivalence_check("((p", "F coffee", ["coffee"])

    def test_validation_error_propagates(self):
        with self.assertRaises(ValueError):
            run_equivalence_check("F coffee", "F coffe", COFFEE_APS)


class ValidateFormulaSubmissionTests(SimpleTestCase):
    def test_undeclared_ap_message(self):
        with self.assertRaises(ValueError) as cm:
            validate_formula_submission("F coffe", COFFEE_APS)
        message = str(cm.exception)
        self.assertIn("coffe", message)
        self.assertIn("Use only the listed atomic propositions.", message)

    def test_empty_declared_aps_rejects_any_ap(self):
        with self.assertRaises(ValueError):
            validate_formula_submission("F p", [])

    def test_unparseable_formula_message_prefix(self):
        with self.assertRaises(ValueError) as cm:
            validate_formula_submission("((p", PQ)
        self.assertTrue(str(cm.exception).startswith("Invalid LTL formula:"))

    def test_temporal_operator_cap(self):
        formula = "X " * 11 + "p"
        with self.assertRaises(ValueError) as cm:
            validate_formula_submission(formula, PQ)
        self.assertIn("10", str(cm.exception))

    def test_distinct_ap_cap(self):
        aps = ["a", "b", "c", "d", "e", "f", "g", "h", "i"]
        formula = " & ".join(aps)
        with self.assertRaises(ValueError) as cm:
            validate_formula_submission(formula, aps)
        self.assertIn("8", str(cm.exception))

    def test_node_cap(self):
        aps = ["a", "b", "c", "d", "e", "f", "g", "h"]
        terms = [f"({x} & !{y})" for x, y in combinations(aps, 2)]
        formula = " | ".join(terms)
        with self.assertRaises(ValueError) as cm:
            validate_formula_submission(formula, aps)
        self.assertIn("40", str(cm.exception))

    def test_strict_subset_of_declared_aps_ok(self):
        self.assertIsNone(validate_formula_submission("F p", ["p", "q", "r"]))

    def test_valid_submission_returns_none(self):
        self.assertIsNone(validate_formula_submission("G(p -> F q)", PQ))
