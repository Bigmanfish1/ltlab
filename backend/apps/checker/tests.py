"""Tests for the LTL checker engine and views.

Engine tests that need SPOT (cytoscape_to_kripke, check_ltl, the lasso
evaluator and analyze_lasso) are decorated with @skipUnless(SPOT_AVAILABLE, …)
so the suite still passes in CI environments where SPOT is not installed.

View tests use unittest.mock to patch the engine so they run without SPOT.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase

from .engine import (
    KIND_LIVENESS,
    KIND_SAFETY,
    STATUS_PENDING,
    STATUS_SATISFIED,
    STATUS_VACUOUS,
    STATUS_VIOLATING,
    _normalize_formula,
    analyze_lasso,
)

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


# ── 2. analyze_lasso fallback (no SPOT formula needed) ───────────────────────

class TestAnalyzeLassoFallback(TestCase):
    """When the formula cannot be parsed, analyze_lasso degrades gracefully to a
    structural prefix/cycle split. An unparseable formula forces this path
    regardless of whether SPOT is installed."""

    def _run(self):
        graph = _graph(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
        )
        return analyze_lasso(
            prefix=[{"spot_id": 0}],
            cycle=[{"spot_id": 1}, {"spot_id": 0}],
            formula_str="@@@ not a formula @@@",
            graph=graph,
            spot_id_to_node={0: "s0", 1: "s1"},
        )

    def test_returns_expected_keys(self):
        out = self._run()
        self.assertIn("steps", out)
        self.assertIn("violation_kind", out)
        self.assertIn("violating_subformula", out)

    def test_steps_have_new_schema(self):
        out = self._run()
        for step in out["steps"]:
            for key in ("state", "props", "status", "highlight",
                        "reason", "in_cycle", "cycle_back"):
                self.assertIn(key, step)
            self.assertNotIn("ok", step)

    def test_prefix_satisfied_cycle_violating(self):
        steps = self._run()["steps"]
        self.assertEqual(steps[0]["status"], STATUS_SATISFIED)   # prefix
        self.assertEqual(steps[0]["in_cycle"], False)
        self.assertEqual(steps[1]["status"], STATUS_VIOLATING)   # cycle
        self.assertTrue(steps[1]["in_cycle"])

    def test_last_step_is_cycle_back(self):
        steps = self._run()["steps"]
        self.assertTrue(steps[-1]["cycle_back"])
        self.assertEqual(sum(1 for s in steps if s["cycle_back"]), 1)


# ── 3. Lasso word evaluator (LTL semantics over an ultimately periodic word) ─

@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed")
class TestLassoWord(TestCase):

    def _word(self, props_by_pos, prefix_len):
        from .engine import _LassoWord, _op_constants
        return _LassoWord(props_by_pos, prefix_len, _op_constants())

    def _f(self, s):
        import spot
        return spot.formula(_normalize_formula(s))

    def test_atomic_proposition(self):
        w = self._word([["p"], []], 0)
        self.assertTrue(w.holds(self._f("p"), 0))
        self.assertFalse(w.holds(self._f("p"), 1))

    def test_position_wraps_into_cycle(self):
        # Pure cycle of length 2: p, (nothing), p, (nothing), …
        w = self._word([["p"], []], 0)
        self.assertTrue(w.holds(self._f("p"), 2))    # wraps to pos 0
        self.assertFalse(w.holds(self._f("p"), 3))   # wraps to pos 1

    def test_next(self):
        w = self._word([["p"], ["q"]], 0)
        self.assertTrue(w.holds(self._f("X q"), 0))
        self.assertTrue(w.holds(self._f("X p"), 1))  # wraps back to p

    def test_globally(self):
        self.assertTrue(self._word([["p"], ["p"]], 0).holds(self._f("G p"), 0))
        self.assertFalse(self._word([["p"], []], 0).holds(self._f("G p"), 0))

    def test_finally(self):
        self.assertTrue(self._word([[], ["q"]], 0).holds(self._f("F q"), 0))
        self.assertFalse(self._word([[], []], 0).holds(self._f("F q"), 0))

    def test_until(self):
        # p holds at 0,1 then q at 2 — but only a 2-state cycle here:
        w = self._word([["p"], ["q"]], 0)
        self.assertTrue(w.holds(self._f("p U q"), 0))
        # q never holds → until fails
        w2 = self._word([["p"], ["p"]], 0)
        self.assertFalse(w2.holds(self._f("p U q"), 0))

    def test_weak_until_holds_forever(self):
        # p forever, q never: p W q is true (weak until), p U q is false
        w = self._word([["p"], ["p"]], 0)
        self.assertTrue(w.holds(self._f("p W q"), 0))
        self.assertFalse(w.holds(self._f("p U q"), 0))

    def test_release(self):
        # q holds forever, p never: q is "released" by nothing but stays true
        w = self._word([["q"], ["q"]], 0)
        self.assertTrue(w.holds(self._f("p R q"), 0))
        # q drops without p ever holding → release violated
        w2 = self._word([["q"], []], 0)
        self.assertFalse(w2.holds(self._f("p R q"), 0))

    def test_nested_response(self):
        # G (p -> F q): with q never present and p present, this is false
        w = self._word([["p"], []], 0)
        self.assertFalse(w.holds(self._f("G (p -> F q)"), 0))
        # with q reachable it is true
        w2 = self._word([["p"], ["q"]], 0)
        self.assertTrue(w2.holds(self._f("G (p -> F q)"), 0))


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
        from .engine import cytoscape_to_kripke
        with self.assertRaises(ValueError) as ctx:
            cytoscape_to_kripke(_graph(
                [{"id": "s0", "props": ["p"], "initial": True},
                 {"id": "s1", "props": ["q"]}],
                [("s0", "s1")],
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
        self.assertNotIn("phantom_s0", id_map.values())

    def test_self_loop_is_allowed(self):
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
        result, _ = self._run(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": ["p"]}],
            [("s0", "s1"), ("s1", "s0")],
            "G p",
        )
        self.assertEqual(result["result"], "satisfied")

    def test_G_p_violated_when_state_lacks_p(self):
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
        result, _ = self._run(
            [{"id": "s0", "props": [], "initial": True},
             {"id": "s1", "props": ["q"]}],
            [("s0", "s1"), ("s1", "s0")],
            "F q",
        )
        self.assertEqual(result["result"], "satisfied")

    def test_F_q_violated_when_q_unreachable(self):
        result, _ = self._run(
            [{"id": "s0", "props": ["p"], "initial": True}],
            [("s0", "s0")],
            "F q",
        )
        self.assertEqual(result["result"], "violated")

    def test_unicode_formula_works(self):
        result, _ = self._run(
            [{"id": "s0", "props": ["p", "q"], "initial": True}],
            [("s0", "s0")],
            "G (p → F q)",
        )
        self.assertEqual(result["result"], "satisfied")

    def test_invalid_formula_raises_value_error(self):
        from .engine import check_ltl, cytoscape_to_kripke
        kripke, bdd_dict, _ = cytoscape_to_kripke(
            _graph([{"id": "s0", "initial": True}], [("s0", "s0")])
        )
        with self.assertRaises(ValueError):
            check_ltl(kripke, bdd_dict, "NOT VALID ### FORMULA")

    def test_counterexample_has_lasso_structure(self):
        result, _ = self._run(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        self.assertEqual(result["result"], "violated")
        self.assertTrue(len(result["prefix"]) + len(result["cycle"]) > 0)

    def test_mutual_exclusion_holds(self):
        result, _ = self._run(
            [{"id": "n",  "props": [],         "initial": True},
             {"id": "c1", "props": ["c1"]},
             {"id": "c2", "props": ["c2"]}],
            [("n", "c1"), ("n", "c2"), ("c1", "n"), ("c2", "n")],
            "G ¬(c1 ∧ c2)",
        )
        self.assertEqual(result["result"], "satisfied")

    def test_request_grant_holds(self):
        result, _ = self._run(
            [{"id": "idle",  "props": [],          "initial": True},
             {"id": "req",   "props": ["req"]},
             {"id": "grant", "props": ["grant"]}],
            [("idle", "req"), ("req", "grant"), ("grant", "idle")],
            "G (req → F grant)",
        )
        self.assertEqual(result["result"], "satisfied")


# ── 6. analyze_lasso end-to-end (per-state classification) ───────────────────

@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed")
class TestAnalyzeLasso(TestCase):

    def _analyze(self, nodes, edges, formula):
        from .engine import check_ltl, cytoscape_to_kripke
        graph = _graph(nodes, edges)
        kripke, bdd_dict, id_map = cytoscape_to_kripke(graph)
        result = check_ltl(kripke, bdd_dict, formula)
        self.assertEqual(result["result"], "violated", "expected a violation")
        return analyze_lasso(
            result["prefix"], result["cycle"], formula, graph, id_map
        )

    def _status_by_state(self, out):
        """Map each state id to the set of statuses it appears with."""
        by_state = {}
        for s in out["steps"]:
            by_state.setdefault(s["state"], set()).add(s["status"])
        return by_state

    # ── G p : only the state missing p is the culprit ──────────────────────────
    def test_G_p_marks_only_offending_state(self):
        out = self._analyze(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
            "G p",
        )
        by_state = self._status_by_state(out)
        self.assertEqual(out["violation_kind"], KIND_SAFETY)
        self.assertIn(STATUS_VIOLATING, by_state.get("s1", set()))
        self.assertNotIn(STATUS_VIOLATING, by_state.get("s0", set()))

    # ── G (p -> F q) : vacuous where p is false; not every state is wrong ──────
    def test_response_property_does_not_mark_every_state(self):
        # q is never reachable, so the p-state breaks the formula while the
        # p-free states carry no obligation (vacuous) — not all states are wrong.
        out = self._analyze(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []},
             {"id": "s2", "props": []}],
            [("s0", "s1"), ("s1", "s2"), ("s2", "s0")],
            "G (p → F q)",
        )
        statuses = [s["status"] for s in out["steps"]]
        # The whole cycle must NOT be uniformly violating.
        self.assertFalse(all(s == STATUS_VIOLATING for s in statuses))
        by_state = self._status_by_state(out)
        # States without p are vacuous (the implication does not apply there).
        self.assertNotIn(STATUS_VIOLATING, by_state.get("s1", set()))
        self.assertNotIn(STATUS_VIOLATING, by_state.get("s2", set()))
        # The p-state is the genuine culprit.
        self.assertIn(STATUS_VIOLATING, by_state.get("s0", set()))

    def test_response_property_marks_p_state_violating(self):
        # q is never reachable → the p-state is where the formula breaks.
        out = self._analyze(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
            "G (p → F q)",
        )
        by_state = self._status_by_state(out)
        self.assertIn(STATUS_VIOLATING, by_state.get("s0", set()))
        self.assertNotIn(STATUS_VIOLATING, by_state.get("s1", set()))

    # ── F q : liveness — no single state is wrong ──────────────────────────────
    def test_F_q_is_liveness_with_no_violating_state(self):
        out = self._analyze(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F q",
        )
        self.assertEqual(out["violation_kind"], KIND_LIVENESS)
        statuses = {s["status"] for s in out["steps"]}
        self.assertNotIn(STATUS_VIOLATING, statuses)
        self.assertIn(STATUS_PENDING, statuses)

    # ── p U q : safety when p breaks before q ──────────────────────────────────
    def test_until_marks_state_where_guard_breaks(self):
        out = self._analyze(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
            "p U q",
        )
        by_state = self._status_by_state(out)
        self.assertIn(STATUS_PENDING, by_state.get("s0", set()))
        self.assertIn(STATUS_VIOLATING, by_state.get("s1", set()))

    # ── X q : only the next state is constrained ───────────────────────────────
    def test_next_marks_only_second_state(self):
        out = self._analyze(
            [{"id": "s0", "props": [], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
            "X q",
        )
        steps = out["steps"]
        self.assertEqual(steps[0]["status"], STATUS_PENDING)
        self.assertEqual(steps[1]["status"], STATUS_VIOLATING)
        # Any state beyond the next one is unconstrained (vacuous).
        for s in steps[2:]:
            self.assertEqual(s["status"], STATUS_VACUOUS)

    # ── Schema guarantees ──────────────────────────────────────────────────────
    def test_steps_have_required_keys(self):
        out = self._analyze(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        for step in out["steps"]:
            for key in ("state", "props", "status", "highlight",
                        "reason", "in_cycle", "cycle_back"):
                self.assertIn(key, step)
            self.assertIn(step["status"],
                          {STATUS_SATISFIED, STATUS_VACUOUS,
                           STATUS_PENDING, STATUS_VIOLATING})

    def test_exactly_one_cycle_back(self):
        out = self._analyze(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        cycle_back = [s for s in out["steps"] if s["cycle_back"]]
        self.assertEqual(len(cycle_back), 1)
        self.assertTrue(out["steps"][-1]["cycle_back"])

    def test_reason_is_nonempty(self):
        out = self._analyze(
            [{"id": "s0", "props": [], "initial": True}],
            [("s0", "s0")],
            "F p",
        )
        for step in out["steps"]:
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

    @patch("apps.checker.views.run_ltl_check")
    def test_valid_request_runs_synchronously_and_renders_holds(self, mock_run):
        # The check runs in-process; a satisfied result renders the holds drawer.
        mock_run.return_value = {
            "result": "satisfied",
            "formula": "G p",
            "kripke_graph": json.loads(self.graph),
        }
        resp = self._post("G p")
        self.assertEqual(resp.status_code, 200)
        mock_run.assert_called_once()
        self.assertIn(b"holds", resp.content)

    @patch("apps.checker.views.run_ltl_check")
    def test_valid_request_violated_renders_violated(self, mock_run):
        mock_run.return_value = {
            "result": "violated",
            "formula": "G p",
            "kripke_graph": json.loads(self.graph),
            "violation_kind": "safety",
            "violating_subformula": "p",
            "trace": [
                {"state": "s0", "props": ["p"], "status": "satisfied",
                 "highlight": "p", "reason": "ok here",
                 "in_cycle": False, "cycle_back": False},
                {"state": "s1", "props": [], "status": "violating",
                 "highlight": "p", "reason": "fails here",
                 "in_cycle": True, "cycle_back": True},
            ],
        }
        resp = self._post("G p")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"violated", resp.content)
        # The violating state must be threaded into the View Counterexample form.
        self.assertIn(b"s1", resp.content)

    @patch("apps.checker.views.run_ltl_check")
    def test_engine_value_error_renders_error_verbatim(self, mock_run):
        # A ValueError from the engine carries a clean user-facing message.
        mock_run.side_effect = ValueError("Invalid formula")
        resp = self._post("G p")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Invalid formula", resp.content)


# ── 9. View: _build_result_context (status-driven derivations) ───────────────

class TestBuildResultContext(TestCase):

    def _ctx(self, engine_result, graph):
        from .views import _build_result_context
        return _build_result_context(engine_result, json.dumps(graph))

    def test_violating_states_taken_from_status(self):
        graph = _graph(
            [{"id": "s0", "props": ["p"], "initial": True},
             {"id": "s1", "props": []}],
            [("s0", "s1"), ("s1", "s0")],
        )
        engine_result = {
            "result": "violated",
            "formula": "G p",
            "violation_kind": "safety",
            "violating_subformula": "p",
            "trace": [
                {"state": "s0", "props": ["p"], "status": "satisfied",
                 "highlight": "p", "reason": "", "in_cycle": False, "cycle_back": False},
                {"state": "s1", "props": [], "status": "violating",
                 "highlight": "p", "reason": "", "in_cycle": True, "cycle_back": True},
            ],
        }
        ctx = self._ctx(engine_result, graph)
        self.assertEqual(ctx["status"], "violated")
        self.assertEqual(json.loads(ctx["violating_states_json"]), ["s1"])
        self.assertEqual(ctx["violation_kind"], "safety")
        self.assertEqual(ctx["violating_subformula"], "p")

    def test_liveness_has_no_violating_states(self):
        graph = _graph([{"id": "s0", "props": [], "initial": True}], [("s0", "s0")])
        engine_result = {
            "result": "violated",
            "formula": "F q",
            "violation_kind": "liveness",
            "violating_subformula": "q",
            "trace": [
                {"state": "s0", "props": [], "status": "pending",
                 "highlight": "q", "reason": "", "in_cycle": True, "cycle_back": True},
            ],
        }
        ctx = self._ctx(engine_result, graph)
        self.assertEqual(json.loads(ctx["violating_states_json"]), [])
        self.assertEqual(ctx["violation_kind"], "liveness")


# ── 10. View: counterexample ─────────────────────────────────────────────────

class TestCounterexampleView(TestCase):

    def _post(self, data):
        from .views import counterexample
        req = RequestFactory().post("/sandbox/counterexample/", data)
        req.supabase_user = MagicMock()
        req.profile = MagicMock()
        return counterexample(req)

    def test_renders_with_valid_trace(self):
        trace = json.dumps([
            {"state": "s0", "props": ["p"], "status": "satisfied",
             "highlight": "p", "reason": "ok", "in_cycle": False, "cycle_back": False},
            {"state": "s1", "props": [], "status": "violating",
             "highlight": "p", "reason": "fail", "in_cycle": True, "cycle_back": True},
        ])
        resp = self._post({
            "formula": "G p",
            "graph_data": json.dumps({"elements": {"nodes": [], "edges": []}}),
            "trace_json": trace,
            "violating_states_json": '["s1"]',
            "violating_edges_json": "[]",
            "violating_subformula": "p",
            "violation_kind": "safety",
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
            "violation_kind": "safety",
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
            "violation_kind": "safety",
        })
        self.assertEqual(resp.status_code, 200)
