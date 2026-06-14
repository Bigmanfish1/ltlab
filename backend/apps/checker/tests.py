"""Tests for the LTL checker engine and views.

Engine tests (cytoscape_to_kripke, check_ltl, lasso_to_trace_steps) are
decorated with @skipUnless(SPOT_AVAILABLE, ...) so the test suite passes
cleanly in CI environments where SPOT is not yet installed.

View tests use unittest.mock to patch the engine so they run without SPOT.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase

from .engine import _normalize_formula, _top_op, _highlight, _reason

try:
    import spot as _spot  # noqa: F401
    SPOT_AVAILABLE = True
except ImportError:
    SPOT_AVAILABLE = False


# ── Helper: minimal Cytoscape graph dicts ────────────────────────────────────

def _graph(nodes, edges=None):
    """Build a minimal Cytoscape-JSON-style dict for tests."""
    node_els = [{"data": dict(id=n["id"], name=n["id"],
                              props=n.get("props", []),
                              initial=n.get("initial", False))}
                for n in nodes]
    edge_els = [{"data": dict(id=f"e{i}", source=e[0], target=e[1])}
                for i, e in enumerate(edges or [])]
    return {"elements": {"nodes": node_els, "edges": edge_els}}


# ── 1. Formula normalisation ─────────────────────────────────────────────────

class TestNormalizeFormula(TestCase):

    def test_unicode_not(self):
        self.assertEqual(_normalize_formula("¬p"), "!p")

    def test_unicode_and(self):
        self.assertEqual(_normalize_formula("p ∧ q"), "p & q")

    def test_unicode_or(self):
        self.assertEqual(_normalize_formula("p ∨ q"), "p | q")

    def test_unicode_implies(self):
        self.assertEqual(_normalize_formula("p → q"), "p -> q")

    def test_unicode_equiv(self):
        self.assertEqual(_normalize_formula("p ↔ q"), "p <-> q")

    def test_combined(self):
        result = _normalize_formula("G (p → F q)")
        self.assertEqual(result, "G (p -> F q)")

    def test_no_change_for_ascii(self):
        s = "G (p -> F q)"
        self.assertEqual(_normalize_formula(s), s)

    def test_empty(self):
        self.assertEqual(_normalize_formula(""), "")


# ── 2. Top-op extraction (formula-type detection) ────────────────────────────

@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed")
class TestTopOp(TestCase):

    def _f(self, s):
        import spot
        return spot.formula(s)

    def test_g(self):
        op, inner = _top_op(self._f("G p"))
        self.assertEqual(op, "G")
        self.assertEqual(inner, "p")

    def test_f(self):
        op, inner = _top_op(self._f("F q"))
        self.assertEqual(op, "F")
        self.assertEqual(inner, "q")

    def test_x(self):
        op, inner = _top_op(self._f("X p"))
        self.assertEqual(op, "X")
        self.assertEqual(inner, "p")

    def test_u(self):
        op, inner = _top_op(self._f("p U q"))
        self.assertEqual(op, "U")
        self.assertIn("p", inner)
        self.assertIn("q", inner)

    def test_not(self):
        op, inner = _top_op(self._f("!p"))
        self.assertEqual(op, "Not")
        self.assertEqual(inner, "p")

    def test_none(self):
        op, inner = _top_op(None)
        self.assertEqual(op, "unknown")
        self.assertEqual(inner, "")


# ── 3. Highlight and reason helpers ─────────────────────────────────────────

class TestHighlightAndReason(TestCase):
    """These helpers use string matching only — no SPOT required."""

    def test_highlight_G_cycle_returns_inner(self):
        h = _highlight(False, ["p"], "G p", "G", "p")
        self.assertEqual(h, "p")

    def test_highlight_G_prefix_returns_prop(self):
        h = _highlight(True, ["p"], "G p", "G", "p")
        self.assertEqual(h, "p")

    def test_highlight_F_cycle_returns_F_inner(self):
        h = _highlight(False, [], "G (p → F q)", "F", "q")
        self.assertIn("q", h)

    def test_highlight_U_cycle_returns_rhs(self):
        h = _highlight(False, [], "p U q", "U", "p U q")
        self.assertEqual(h, "q")

    def test_reason_cycle_back_always_explains_loop(self):
        r = _reason(False, [], "G p", "G", "p", True)
        self.assertIn("loop", r.lower())

    def test_reason_G_ok_step(self):
        r = _reason(True, ["p"], "G p", "G", "p", False)
        self.assertIn("holds", r.lower())

    def test_reason_G_violation(self):
        r = _reason(False, [], "G p", "G", "p", False)
        self.assertIn("every", r.lower())

    def test_reason_F_violation(self):
        r = _reason(False, [], "F q", "F", "q", False)
        self.assertIn("eventually", r.lower())

    def test_reason_U_ok(self):
        r = _reason(True, ["p"], "p U q", "U", "p U q", False)
        self.assertIn("until", r.lower())

    def test_reason_fallback_ok(self):
        r = _reason(True, ["p"], "p", "unknown", "", False)
        self.assertIn("satisfied", r.lower())

    def test_reason_fallback_violation(self):
        r = _reason(False, [], "p", "unknown", "", False)
        self.assertIn("fails", r.lower())


# ── 4. cytoscape_to_kripke ───────────────────────────────────────────────────

@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed")
class TestCytoscapeToKripke(TestCase):

    def _convert(self, nodes, edges=None):
        from .engine import cytoscape_to_kripke
        return cytoscape_to_kripke(_graph(nodes, edges))

    def test_basic_two_node_graph(self):
        kripke, bdd_dict, id_map = self._convert(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": ["q"]}],
            [("s0", "s1"), ("s1", "s0")],
        )
        self.assertIsNotNone(kripke)
        self.assertEqual(len(id_map), 2)

    def test_deadlock_state_raises(self):
        """A state with no outgoing transition breaks Kripke totality."""
        from .engine import cytoscape_to_kripke
        with self.assertRaises(ValueError) as ctx:
            cytoscape_to_kripke(_graph(
                [{"id": "s0", "props": ["p"], "initial": True},
                 {"id": "s1", "props": ["q"]}],
                [("s0", "s1")],   # s1 has no successor → deadlock
            ))
        msg = str(ctx.exception).lower()
        self.assertIn("outgoing", msg)
        self.assertIn("s1", str(ctx.exception))

    def test_no_nodes_raises(self):
        from .engine import cytoscape_to_kripke
        with self.assertRaises(ValueError):
            cytoscape_to_kripke({"elements": {"nodes": [], "edges": []}})

    def test_no_initial_raises(self):
        from .engine import cytoscape_to_kripke
        with self.assertRaises(ValueError) as ctx:
            cytoscape_to_kripke(_graph([{"id": "s0", "props": []}]))
        self.assertIn("initial", str(ctx.exception).lower())

    def test_multiple_initial_raises(self):
        from .engine import cytoscape_to_kripke
        with self.assertRaises(ValueError) as ctx:
            cytoscape_to_kripke(_graph([
                {"id": "s0", "initial": True},
                {"id": "s1", "initial": True},
            ]))
        self.assertIn("multiple", str(ctx.exception).lower())

    def test_phantom_nodes_are_excluded(self):
        from .engine import cytoscape_to_kripke
        graph = {
            "elements": {
                "nodes": [
                    {"data": {"id": "s0", "props": ["p"], "initial": True}},
                    {"data": {"id": "phantom_s0", "phantom": True}},
                ],
                "edges": [
                    {"data": {"source": "s0", "target": "s0"}},
                ],
            }
        }
        kripke, bdd_dict, id_map = cytoscape_to_kripke(graph)
        # Only real nodes should appear in the map
        self.assertNotIn("phantom_s0", id_map.values())

    def test_self_loop_is_allowed(self):
        """Kripke graphs may have self-loops (required for some formulas)."""
        kripke, bdd_dict, id_map = self._convert(
            [{"id": "s0", "props": ["p"], "initial": True}],
            [("s0", "s0")],
        )
        self.assertIsNotNone(kripke)


# ── 5. check_ltl ────────────────────────────────────────────────────────────

@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed")
class TestCheckLTL(TestCase):

    def _run(self, nodes, edges, formula):
        from .engine import check_ltl, cytoscape_to_kripke
        kripke, bdd_dict, id_map = cytoscape_to_kripke(_graph(nodes, edges))
        result = check_ltl(kripke, bdd_dict, formula)
        return result, id_map

    def test_G_p_holds_on_all_p_graph(self):
        """G p should hold when every state has p."""
        result, _ = self._run(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": ["p"]}],
            [("s0", "s1"), ("s1", "s0")],
            "G p",
        )
        self.assertEqual(result["result"], "satisfied")

    def test_G_p_violated_when_state_lacks_p(self):
        """G p should be violated when a reachable state lacks p."""
        result, _ = self._run(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
            "G p",
        )
        self.assertEqual(result["result"], "violated")
        self.assertIn("prefix", result)
        self.assertIn("cycle", result)

    def test_F_q_holds_when_q_reachable(self):
        """F q should hold when q is reachable from the initial state."""
        result, _ = self._run(
            [{"id": "s0", "props": [], "initial": True},
             {"id": "s1", "props": ["q"]}],
            [("s0", "s1"), ("s1", "s0")],
            "F q",
        )
        self.assertEqual(result["result"], "satisfied")

    def test_F_q_violated_when_q_unreachable(self):
        """F q should be violated when no state ever has q."""
        result, _ = self._run(
            [{"id": "s0", "props": ["p"], "initial": True}],
            [("s0", "s0")],
            "F q",
        )
        self.assertEqual(result["result"], "violated")

    def test_unicode_formula_works(self):
        """Unicode operators (¬, ∧, →) must be normalised before SPOT parsing."""
        result, _ = self._run(
            [{"id": "s0", "props": ["p", "q"], "initial": True}],
            [("s0", "s0")],
            "G (p → F q)",   # uses Unicode →
        )
        # With p & q both always true, G(p → F q) holds
        self.assertEqual(result["result"], "satisfied")

    def test_invalid_formula_raises_value_error(self):
        from .engine import check_ltl, cytoscape_to_kripke
        kripke, bdd_dict, _ = cytoscape_to_kripke(
            _graph([{"id": "s0", "initial": True}], [("s0", "s0")])
        )
        with self.assertRaises(ValueError):
            check_ltl(kripke, bdd_dict, "NOT VALID ### FORMULA")

    def test_counterexample_has_lasso_structure(self):
        """Violated check must return non-empty prefix and/or cycle."""
        result, _ = self._run(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        self.assertEqual(result["result"], "violated")
        # At least one of prefix or cycle must be non-empty
        self.assertTrue(len(result["prefix"]) + len(result["cycle"]) > 0)

    def test_mutual_exclusion_holds(self):
        """Classic mutual exclusion: G ¬(c1 ∧ c2) should hold on a correct graph."""
        result, _ = self._run(
            [{"id": "n",  "props": [],         "initial": True},
             {"id": "c1", "props": ["c1"]},
             {"id": "c2", "props": ["c2"]}],
            [("n", "c1"), ("n", "c2"), ("c1", "n"), ("c2", "n")],
            "G ¬(c1 ∧ c2)",
        )
        self.assertEqual(result["result"], "satisfied")

    def test_request_grant_holds(self):
        """G (req → F grant) should hold on the request-grant graph."""
        result, _ = self._run(
            [{"id": "idle",  "props": [],          "initial": True},
             {"id": "req",   "props": ["req"]},
             {"id": "grant", "props": ["grant"]}],
            [("idle", "req"), ("req", "grant"), ("grant", "idle")],
            "G (req → F grant)",
        )
        self.assertEqual(result["result"], "satisfied")


# ── 6. lasso_to_trace_steps ──────────────────────────────────────────────────

@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed")
class TestLassoToTraceSteps(TestCase):

    def _full_check(self, nodes, edges, formula):
        from .engine import check_ltl, cytoscape_to_kripke, lasso_to_trace_steps
        graph = _graph(nodes, edges)
        kripke, bdd_dict, id_map = cytoscape_to_kripke(graph)
        result = check_ltl(kripke, bdd_dict, formula)
        self.assertEqual(result["result"], "violated")
        return lasso_to_trace_steps(
            result["prefix"], result["cycle"], formula, graph, id_map
        )

    def test_steps_have_required_keys(self):
        steps = self._full_check(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        for step in steps:
            for key in ("state", "props", "ok", "highlight", "reason", "cycle_back"):
                self.assertIn(key, step)

    def test_prefix_steps_are_ok(self):
        steps = self._full_check(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s1")],
            "G p",
        )
        prefix_steps = [s for s in steps if s["ok"]]
        cycle_steps  = [s for s in steps if not s["ok"]]
        # There must be at least one cycle step for a violation
        self.assertTrue(len(cycle_steps) > 0)
        # Prefix steps all have ok=True
        self.assertTrue(all(s["ok"] for s in prefix_steps))

    def test_exactly_one_cycle_back(self):
        """The last cycle step must be marked cycle_back=True."""
        steps = self._full_check(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        cycle_back_steps = [s for s in steps if s["cycle_back"]]
        self.assertEqual(len(cycle_back_steps), 1)
        # Must be the last step
        self.assertTrue(steps[-1]["cycle_back"])

    def test_state_names_match_graph_node_ids(self):
        steps = self._full_check(
            [{"id": "idle",  "props": [], "initial": True},
             {"id": "active","props": ["a"]}],
            [("idle", "active"), ("active", "idle")],
            "G ¬a",
        )
        valid_ids = {"idle", "active"}
        for step in steps:
            self.assertIn(step["state"], valid_ids)

    def test_props_match_node_props(self):
        steps = self._full_check(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
            "G p",
        )
        for step in steps:
            if step["state"] == "s0":
                self.assertIn("p", step["props"])
            elif step["state"] == "s1":
                self.assertEqual(step["props"], [])

    def test_reason_is_nonempty_string(self):
        steps = self._full_check(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        for step in steps:
            self.assertIsInstance(step["reason"], str)
            self.assertTrue(len(step["reason"]) > 0)


# ── 7. View: verify_ltl ─────────────────────────────────────────────────────

class TestVerifyLTLView(TestCase):
    """These tests mock the engine so they run without SPOT installed."""

    def setUp(self):
        self.factory = RequestFactory()
        self.graph = json.dumps(_graph(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
        ))

    def _post(self, formula, graph=None):
        from .views import verify_ltl
        req = self.factory.post("/sandbox/verify/", {
            "formula": formula,
            "graph_data": graph or self.graph,
        })
        req.supabase_user = MagicMock()
        req.profile = MagicMock()
        return verify_ltl(req)

    def test_empty_formula_returns_error(self):
        resp = self._post("")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"No formula provided", resp.content)

    def test_empty_graph_returns_error(self):
        resp = self._post("G p", graph=json.dumps({"elements": {"nodes": [], "edges": []}}))
        self.assertIn(b"Graph is empty", resp.content)

    def test_no_initial_state_returns_error(self):
        graph = json.dumps(_graph([{"id": "s0", "props": ["p"], "initial": False}]))
        resp = self._post("G p", graph=graph)
        self.assertIn(b"No initial state", resp.content)

    def test_multiple_initial_states_returns_error(self):
        graph = json.dumps(_graph([
            {"id": "s0", "props": [], "initial": True},
            {"id": "s1", "props": [], "initial": True},
        ], [("s0", "s1"), ("s1", "s0")]))
        resp = self._post("G p", graph=graph)
        self.assertIn(b"Multiple initial", resp.content)

    @patch("apps.checker.views.cytoscape_to_kripke")
    @patch("apps.checker.views.check_ltl")
    def test_satisfied_result_renders_holds(self, mock_check, mock_convert):
        mock_convert.return_value = (MagicMock(), MagicMock(), {})
        mock_check.return_value = {"result": "satisfied"}
        resp = self._post("G p")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"holds", resp.content)

    @patch("apps.checker.views.lasso_to_trace_steps")
    @patch("apps.checker.views.cytoscape_to_kripke")
    @patch("apps.checker.views.check_ltl")
    def test_violated_result_renders_violated(self, mock_check, mock_convert, mock_lasso):
        mock_convert.return_value = (MagicMock(), MagicMock(), {0: "s0", 1: "s1"})
        mock_check.return_value = {
            "result": "violated",
            "prefix": [{"spot_id": 0}],
            "cycle":  [{"spot_id": 1}],
        }
        mock_lasso.return_value = [
            {"state": "s0", "props": ["p"], "ok": True,
             "highlight": "p", "reason": "ok here", "cycle_back": False},
            {"state": "s1", "props": [], "ok": False,
             "highlight": "G p", "reason": "fails here", "cycle_back": True},
        ]
        resp = self._post("G p")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"violated", resp.content)

    @patch("apps.checker.views.cytoscape_to_kripke")
    @patch("apps.checker.views.check_ltl")
    def test_invalid_formula_returns_error(self, mock_check, mock_convert):
        mock_convert.return_value = (MagicMock(), MagicMock(), {})
        mock_check.side_effect = ValueError("Invalid LTL formula: unexpected token")
        resp = self._post("INVALID @@@ FORMULA")
        self.assertIn(b"Invalid LTL formula", resp.content)

    @patch("apps.checker.views.cytoscape_to_kripke")
    @patch("apps.checker.views.check_ltl")
    def test_spot_runtime_error_returns_error(self, mock_check, mock_convert):
        mock_convert.return_value = (MagicMock(), MagicMock(), {})
        mock_check.side_effect = RuntimeError("SPOT not installed")
        resp = self._post("G p")
        self.assertIn(b"error", resp.content)


# ── 8. View: counterexample ──────────────────────────────────────────────────

class TestCounterexampleView(TestCase):

    def _post(self, data):
        from .views import counterexample
        req = RequestFactory().post("/sandbox/counterexample/", data)
        req.supabase_user = MagicMock()
        req.profile = MagicMock()
        return counterexample(req)

    def test_renders_with_valid_trace(self):
        trace = json.dumps([
            {"state": "s0", "props": ["p"], "ok": True,
             "highlight": "p", "reason": "ok", "cycle_back": False},
            {"state": "s1", "props": [], "ok": False,
             "highlight": "G p", "reason": "fail", "cycle_back": True},
        ])
        resp = self._post({
            "formula": "G p",
            "graph_data": json.dumps({"elements": {"nodes": [], "edges": []}}),
            "trace_json": trace,
            "violating_states_json": '["s1"]',
            "violating_edges_json": '[["s0","s1"]]',
            "violating_subformula": "p",
        })
        self.assertEqual(resp.status_code, 200)

    def test_renders_with_malformed_trace_json(self):
        resp = self._post({
            "formula": "G p",
            "graph_data": "{}",
            "trace_json": "NOT JSON",
            "violating_states_json": "[]",
            "violating_edges_json": "[]",
            "violating_subformula": "",
        })
        self.assertEqual(resp.status_code, 200)

    def test_renders_with_malformed_graph_json(self):
        resp = self._post({
            "formula": "G p",
            "graph_data": "INVALID",
            "trace_json": "[]",
            "violating_states_json": "[]",
            "violating_edges_json": "[]",
            "violating_subformula": "",
        })
        self.assertEqual(resp.status_code, 200)
