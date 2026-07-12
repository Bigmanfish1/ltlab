from django.test import SimpleTestCase

from apps.checker.tasks import run_equivalence_check

APS = ["p", "q"]


class EquivalenceGradingTests(SimpleTestCase):
    def test_equivalent_rewrite_accepted(self):
        self.assertTrue(run_equivalence_check("G p", "!F!p", APS)["equivalent"])

    def test_until_expansion_accepted(self):
        self.assertTrue(run_equivalence_check("F p", "true U p", APS)["equivalent"])

    def test_unicode_operators_normalised(self):
        self.assertTrue(run_equivalence_check("G (p -> F q)", "G (¬(p ∧ ¬F q))", APS)["equivalent"])

    def test_non_equivalent_rejected(self):
        self.assertFalse(run_equivalence_check("G F p", "F G p", APS)["equivalent"])

    def test_precedence_vs_stability_distinguished(self):
        self.assertFalse(run_equivalence_check("G (p -> F q)", "F G q", APS)["equivalent"])

    def test_submission_echoed(self):
        self.assertEqual(run_equivalence_check("G p", "G p", APS)["formula"], "G p")


class SubmissionValidationTests(SimpleTestCase):
    def test_unparseable_submission_raises(self):
        with self.assertRaisesMessage(ValueError, "Invalid LTL formula"):
            run_equivalence_check("G p", "G (p", APS)

    def test_undeclared_ap_raises(self):
        with self.assertRaisesMessage(ValueError, "not in this exercise: r"):
            run_equivalence_check("G p", "G r", APS)

    def test_temporal_op_cap(self):
        formula = "X " * 11 + "p"
        with self.assertRaisesMessage(ValueError, "temporal operators"):
            run_equivalence_check("G p", formula, APS)

    def test_node_cap(self):
        formula = " & ".join("X " * i + "p" for i in range(9))
        with self.assertRaisesMessage(ValueError, "too complex"):
            run_equivalence_check("G p", formula, APS)

    def test_unparseable_target_raises(self):
        with self.assertRaisesMessage(ValueError, "Invalid LTL formula"):
            run_equivalence_check("G (p", "G p", APS)
