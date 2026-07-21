"""Tests for the formula-equivalence explorer (engine.formula_relationship).

Correctness is checked two ways:
  1. The relationship key matches an INDEPENDENT SPOT ground-truth.
  2. Every generated witness is fed back through the model checker — it must
     genuinely satisfy the formula it claims and violate the other. This is the
     strongest guarantee: the distinguishing model is real, not just plausible.
"""
from django.test import SimpleTestCase

from apps.checker.engine import (
    _SPOT_AVAILABLE,
    _relationship_cached,
    check_ltl,
    cytoscape_to_kripke,
    formula_relationship,
)

try:
    import spot
except ImportError:  # pragma: no cover
    spot = None


def _witness_graph(elements):
    """Split flat cytoscape elements into the {elements:{nodes,edges}} shape."""
    return {
        "elements": {
            "nodes": [e for e in elements if "source" not in e["data"]],
            "edges": [e for e in elements if "source" in e["data"]],
        }
    }


def _models(graph, formula):
    k, d, _ = cytoscape_to_kripke(graph)
    return check_ltl(k, d, formula)["result"] == "satisfied"


class FormulaRelationshipTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _SPOT_AVAILABLE or spot is None:
            raise cls.skipTest(cls, "SPOT not installed")

    def test_identical_is_equivalent(self):
        r = formula_relationship("p", "p")
        self.assertEqual(r["relationship"], "equivalent")
        self.assertEqual(r["witnesses"], [])

    def test_syntactic_variants_are_equivalent(self):
        # spacing / commutativity should not matter (canonicalised).
        r = formula_relationship("a ∧ b", "b & a")
        self.assertEqual(r["relationship"], "equivalent")

    def test_fg_implies_gf(self):
        # F G p is strictly stronger than G F p.
        r = formula_relationship("G F p", "F G p")
        self.assertEqual(r["relationship"], "b_stronger")   # B (F G p) stronger
        self.assertEqual(len(r["witnesses"]), 1)

    def test_direction_is_symmetric(self):
        r = formula_relationship("F G p", "G F p")
        self.assertEqual(r["relationship"], "a_stronger")   # A (F G p) stronger

    def test_incomparable_has_two_witnesses(self):
        r = formula_relationship("p", "!p")
        self.assertEqual(r["relationship"], "incomparable")
        self.assertEqual(len(r["witnesses"]), 2)

    def test_witnesses_actually_distinguish(self):
        """Every witness must model the formula it satisfies and break the other."""
        pairs = [("G F p", "F G p"), ("p", "!p"), ("X p", "p"),
                 ("G(a -> F b)", "G(a -> X b)")]
        for a, b in pairs:
            r = formula_relationship(a, b)
            for w in r["witnesses"]:
                graph = _witness_graph(w["elements"])
                hold, viol = (a, b) if w["satisfies"] == "A" else (b, a)
                self.assertTrue(_models(graph, hold),
                                f"witness for {a!r}/{b!r} should satisfy {hold!r}")
                self.assertFalse(_models(graph, viol),
                                 f"witness for {a!r}/{b!r} should violate {viol!r}")

    def test_witness_trace_is_a_lasso(self):
        r = formula_relationship("G F p", "F G p")
        trace = r["witnesses"][0]["trace"]
        self.assertTrue(trace)
        self.assertTrue(any(s["cycle_start"] for s in trace))
        self.assertTrue(trace[-1]["cycle_back"])

    # ── Formula facts (Tier 3) ───────────────────────────────────────────────
    def test_facts_flag_tautology(self):
        r = formula_relationship("G p | F !p", "true")
        self.assertTrue(r["facts_a"]["tautology"])
        self.assertTrue(r["facts_b"]["tautology"])
        self.assertEqual(r["relationship"], "equivalent")

    def test_facts_flag_contradiction(self):
        r = formula_relationship("p & !p", "q & !q")
        self.assertTrue(r["facts_a"]["contradiction"])
        self.assertFalse(r["facts_a"]["satisfiable"])
        self.assertEqual(r["relationship"], "equivalent")

    def test_facts_temporal_class(self):
        r = formula_relationship("G F p", "F G p")
        self.assertEqual(r["facts_a"]["class_label"], "Recurrence")
        self.assertEqual(r["facts_b"]["class_label"], "Persistence")

    # ── Parallel A/B truth trace ─────────────────────────────────────────────
    def test_witness_position0_is_the_decisive_disagreement(self):
        # Position 0 (the whole run) must satisfy the claimed formula and violate
        # the other: that single disagreement is the proof of non-equivalence.
        r = formula_relationship("X p", "p")
        for w in r["witnesses"]:
            s0 = w["trace"][0]
            self.assertNotEqual(s0["a_holds"], s0["b_holds"])
            self.assertTrue(s0["diverges"])
            if w["satisfies"] == "A":
                self.assertTrue(s0["a_holds"])
                self.assertFalse(s0["b_holds"])
            else:
                self.assertTrue(s0["b_holds"])
                self.assertFalse(s0["a_holds"])

    def test_diverges_flag_tracks_ab_disagreement(self):
        r = formula_relationship("G F p", "F G p")
        for w in r["witnesses"]:
            for s in w["trace"]:
                self.assertEqual(s["diverges"], s["a_holds"] != s["b_holds"])
                self.assertEqual(s["agree"], s["a_holds"] == s["b_holds"])

    def test_difference_character_safety_vs_liveness(self):
        # G F p vs F G p differ only in the limit (liveness); X p vs p differ at
        # a concrete state (safety).
        self.assertEqual(
            formula_relationship("G F p", "F G p")["witnesses"][0]["violation_kind"],
            "liveness",
        )
        self.assertEqual(
            formula_relationship("X p", "p")["witnesses"][0]["violation_kind"],
            "safety",
        )

    def test_no_em_dashes_in_any_prose(self):
        r = formula_relationship("p", "!p")
        for w in r["witnesses"]:
            self.assertNotIn("—", w["divergence"])
            for s in w["trace"]:
                self.assertNotIn("—", s["reason"])

    # ── Shared example (equivalent case) ─────────────────────────────────────
    def test_equivalent_has_shared_example(self):
        r = formula_relationship("F p", "F p")
        self.assertEqual(r["relationship"], "equivalent")
        self.assertIsNotNone(r["shared_example"])
        # the shared behaviour must actually model the formula.
        graph = _witness_graph(r["shared_example"]["elements"])
        self.assertTrue(_models(graph, "F p"))

    def test_shared_example_rows_agree_everywhere(self):
        # Equivalent formulas return the same verdict at every step of any run.
        r = formula_relationship("!(G p)", "F !p")
        self.assertEqual(r["relationship"], "equivalent")
        for s in r["shared_example"]["trace"]:
            self.assertEqual(s["a_holds"], s["b_holds"])
            self.assertTrue(s["agree"])
            self.assertFalse(s["diverges"])

    def test_contradictions_have_no_shared_example(self):
        r = formula_relationship("p & !p", "q & !q")
        self.assertIsNone(r["shared_example"])
        self.assertIn("contradiction", r["note"])

    # ── Interactive timeline data (syntax tree + propositions) ───────────────
    def test_result_carries_formula_ast(self):
        # The timeline renders one row per subformula, so the result must expose
        # the full syntax tree of each formula.
        r = formula_relationship("G F p", "F G p")
        self.assertEqual(r["ast_a"]["op"], "G")
        self.assertEqual(r["ast_a"]["children"][0]["op"], "F")
        self.assertEqual(r["ast_a"]["children"][0]["children"][0]["op"], "ap")
        self.assertEqual(r["ast_b"]["op"], "F")

    def test_aps_are_union_across_both_formulas(self):
        r = formula_relationship("a & b", "c")
        self.assertEqual(set(r["aps"]), {"a", "b", "c"})

    # ── Validation / error paths ─────────────────────────────────────────────
    def test_unparseable_raises_valueerror(self):
        with self.assertRaises(ValueError):
            formula_relationship("a b c", "p")

    def test_combined_ap_cap(self):
        many = "a & b & c & d & e & f & g & h & i"   # 9 distinct APs
        with self.assertRaises(ValueError):
            formula_relationship(many, "a")

    # ── Cache ────────────────────────────────────────────────────────────────
    def test_cache_hits_on_repeat(self):
        _relationship_cached.cache_clear()
        formula_relationship("G F q", "F G q")
        before = _relationship_cached.cache_info()
        formula_relationship("G F q", "F G q")
        after = _relationship_cached.cache_info()
        self.assertEqual(after.hits, before.hits + 1)

    def test_reversed_pair_is_distinct_cache_key(self):
        _relationship_cached.cache_clear()
        formula_relationship("G F r", "F G r")
        formula_relationship("F G r", "G F r")
        # two distinct directional keys → two misses, no hit.
        self.assertEqual(_relationship_cached.cache_info().misses, 2)
