from django.test import SimpleTestCase

from apps.checker.misconceptions import classify_misconception


class ClassifyMisconceptionTests(SimpleTestCase):
    def test_named_operator_slips(self):
        cases = {
            ("F b", "X b"): "f_vs_x",           # next used where eventually needed
            ("G F a", "F G a"): "g_vs_f",        # stabilises vs recurs infinitely
            ("F grant", "G grant"): "g_vs_f",    # plain always-vs-eventually swap
            ("G a", "a"): "missing_global",      # forgot the outer G
            ("F a", "a"): "missing_eventually",  # forgot the F
            ("G a", "F !a"): "inverted",         # negation of the target
            ("G (a -> F b)", "F (a -> F b)"): "g_vs_f",  # single-position outer G->F slip
            ("G (a -> G b)", "G (a -> b)"): "missing_global",  # missing G around a subterm
            ("G (a -> X b)", "G (a -> F b)"): "f_vs_x",  # single-position F->X slip in context
        }
        for (target, submitted), expected in cases.items():
            self.assertEqual(
                classify_misconception(target, submitted), expected,
                msg=f"{submitted!r} vs target {target!r}",
            )

    def test_implication_and_unrelated_fold_into_mistranslation(self):
        # strictly stronger, strictly weaker, and unrelated all fold in
        for target, submitted in [
            ("G a", "G a & F b"),   # stronger than target
            ("G(a & b)", "G a"),    # weaker than target
            ("G a", "F b"),         # unrelated
        ]:
            self.assertEqual(
                classify_misconception(target, submitted), "mistranslation",
                msg=f"{submitted!r} vs target {target!r}",
            )

    def test_equivalent_is_not_a_misconception(self):
        for target, submitted in [("F b", "F b"), ("a & b", "b & a"), ("G a", "a & G a")]:
            self.assertIsNone(classify_misconception(target, submitted))

    def test_sere_rational_operators_do_not_crash(self):
        # SERE/rational operators (Concat {a;b}, Star {a[*]}, Fusion {a:b}) parse but
        # cannot be safely rewritten — must fall through, never abort the process.
        for submitted in ("{a;b}", "{a[*]}", "{a:b}", "{a;b;c}"):
            self.assertEqual(
                classify_misconception("G a", submitted), "mistranslation",
                msg=f"SERE {submitted!r} should be handled, not crash",
            )

    def test_empty_and_unparseable_are_excluded(self):
        # syntax errors are not LTL misconceptions; a malformed target is not the
        # student's fault — all excluded (None), not bucketed
        self.assertIsNone(classify_misconception("G a", ""))
        self.assertIsNone(classify_misconception("", "G a"))
        self.assertIsNone(classify_misconception("G a", "G G ("))   # unparseable submission
        self.assertIsNone(classify_misconception("G G (", "G a"))   # malformed target
        # "asdf" is a valid atomic proposition, not a syntax error → unrelated formula
        self.assertEqual(classify_misconception("G a", "asdf"), "mistranslation")
