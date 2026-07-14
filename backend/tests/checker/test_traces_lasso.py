from django.test import SimpleTestCase

from apps.checker.tasks import run_trace_check
from apps.checker.traces import MAX_TRACE_STATES, evaluate_lasso, validate_lasso


def _node(node_id, props=None, initial=False, phantom=False):
    data = {"id": node_id, "name": node_id, "props": props or []}
    if initial:
        data["initial"] = True
    if phantom:
        data["phantom"] = True
    return {"data": data}


def _edge(edge_id, source, target, phantom=False):
    data = {"id": edge_id, "source": source, "target": target}
    if phantom:
        data["phantom"] = True
    return {"data": data}


def _graph(nodes, edges):
    return {"elements": {"nodes": nodes, "edges": edges}}


G1 = _graph(
    [_node("s0", ["p"], initial=True)],
    [_edge("e0", "s0", "s0")],
)

G2 = _graph(
    [
        _node("s0", ["p"], initial=True),
        _node("s1", ["p"]),
        _node("s2", ["q"]),
    ],
    [
        _edge("e0", "s0", "s1"),
        _edge("e1", "s0", "s2"),
        _edge("e2", "s1", "s1"),
        _edge("e3", "s2", "s2"),
    ],
)

G3 = _graph(
    G2["elements"]["nodes"] + [_node("s3", ["r"])],
    G2["elements"]["edges"] + [_edge("e4", "s3", "s3")],
)

G4 = _graph(
    [
        _node("s0", ["p"], initial=True),
        _node("s1", ["q"], initial=True),
    ],
    [
        _edge("e0", "s0", "s0"),
        _edge("e1", "s1", "s1"),
        _edge("e2", "s0", "s1"),
    ],
)

G5 = _graph(
    G1["elements"]["nodes"] + [_node("ph0", ["z"], phantom=True)],
    G1["elements"]["edges"] + [_edge("pe0", "s0", "ph0", phantom=True)],
)

G6 = _graph(
    [
        _node("s0", ["p"], initial=True),
        _node("s1", ["q"]),
    ],
    [_edge("e0", "s0", "s1")],
)

EMPTY_CYCLE_MSG = "Select a repeating cycle — a lasso needs at least one looping state."
WRONG_START_MSG = "The path must start at the initial state."


class ValidateLassoTests(SimpleTestCase):
    def test_max_trace_states_constant(self):
        self.assertEqual(MAX_TRACE_STATES, 60)

    def test_valid_self_loop_lasso(self):
        self.assertIsNone(validate_lasso(G1, [], ["s0"]))

    def test_valid_lasso_with_prefix(self):
        self.assertIsNone(validate_lasso(G2, ["s0"], ["s2"]))

    def test_empty_cycle_message(self):
        self.assertEqual(validate_lasso(G2, ["s0"], []), EMPTY_CYCLE_MSG)

    def test_unknown_state_message(self):
        error = validate_lasso(G2, ["s0"], ["s9"])
        self.assertTrue(error.startswith("Unknown state(s) in the path: "))
        self.assertIn("s9", error)
        self.assertTrue(error.endswith("."))

    def test_unknown_states_sorted_comma_joined(self):
        error = validate_lasso(G2, ["s0"], ["s9", "s8"])
        self.assertEqual(error, "Unknown state(s) in the path: s8, s9.")

    def test_wrong_start_message(self):
        self.assertEqual(validate_lasso(G2, [], ["s1"]), WRONG_START_MSG)

    def test_missing_edge_within_cycle(self):
        self.assertEqual(
            validate_lasso(G2, ["s0"], ["s1", "s2"]),
            "There is no transition from s1 to s2.",
        )

    def test_missing_cycle_close_edge(self):
        self.assertEqual(
            validate_lasso(G6, ["s0"], ["s1"]),
            "There is no transition from s1 to s1.",
        )

    def test_missing_edge_to_real_unreachable_state(self):
        self.assertEqual(
            validate_lasso(G3, ["s0"], ["s3"]),
            "There is no transition from s0 to s3.",
        )

    def test_multi_initial_first_declared_wins(self):
        self.assertIsNone(validate_lasso(G4, ["s0"], ["s1"]))
        self.assertEqual(validate_lasso(G4, [], ["s1"]), WRONG_START_MSG)

    def test_oversized_trace_raises(self):
        with self.assertRaises(ValueError) as cm:
            validate_lasso(G1, ["s0"] * 60, ["s0"])
        self.assertIn("60", str(cm.exception))

    def test_exactly_sixty_states_allowed(self):
        self.assertIsNone(validate_lasso(G1, ["s0"] * 59, ["s0"]))


class EvaluateLassoTests(SimpleTestCase):
    def test_g1_self_loop(self):
        self.assertTrue(evaluate_lasso(G1, "G p", [], ["s0"]))
        self.assertFalse(evaluate_lasso(G1, "F q", [], ["s0"]))
        self.assertTrue(evaluate_lasso(G1, "X p", [], ["s0"]))

    def test_g2_branching(self):
        self.assertTrue(evaluate_lasso(G2, "F q", ["s0"], ["s2"]))
        self.assertFalse(evaluate_lasso(G2, "F q", ["s0"], ["s1"]))
        self.assertTrue(evaluate_lasso(G2, "G p", ["s0"], ["s1"]))
        self.assertTrue(evaluate_lasso(G2, "p U q", ["s0"], ["s2"]))

    def test_unicode_operators_accepted(self):
        self.assertTrue(evaluate_lasso(G1, "G(p ∨ q)", [], ["s0"]))

    def test_unparseable_formula_raises(self):
        with self.assertRaises(ValueError) as cm:
            evaluate_lasso(G1, "((p", [], ["s0"])
        self.assertTrue(str(cm.exception).startswith("Invalid LTL formula:"))

    def test_phantoms_ignored(self):
        self.assertTrue(evaluate_lasso(G5, "G p", [], ["s0"]))
        self.assertFalse(evaluate_lasso(G5, "F q", [], ["s0"]))
        self.assertTrue(evaluate_lasso(G5, "X p", [], ["s0"]))


class RunTraceCheckTests(SimpleTestCase):
    def test_valid_path_holds(self):
        result = run_trace_check(G2, "F q", ["s0"], ["s2"])
        self.assertEqual(result, {"path_ok": True, "path_error": None, "holds": True})

    def test_valid_path_violated(self):
        result = run_trace_check(G2, "F q", ["s0"], ["s1"])
        self.assertEqual(result, {"path_ok": True, "path_error": None, "holds": False})

    def test_student_path_problem(self):
        result = run_trace_check(G2, "F q", ["s0"], [])
        self.assertEqual(
            result, {"path_ok": False, "path_error": EMPTY_CYCLE_MSG, "holds": None}
        )

    def test_unknown_state_reported(self):
        result = run_trace_check(G2, "F q", ["s0"], ["s9"])
        self.assertFalse(result["path_ok"])
        self.assertIn("s9", result["path_error"])
        self.assertIsNone(result["holds"])

    def test_phantom_graph_matches_plain(self):
        # run_trace_check rejects formulas whose props appear on no state, so
        # parity with G1 is asserted over props that exist in the graph; the
        # contract's F q case is covered at the evaluate_lasso level above.
        for formula, expected in [("G p", True), ("F !p", False), ("X p", True)]:
            with self.subTest(formula=formula):
                result = run_trace_check(G5, formula, [], ["s0"])
                self.assertEqual(
                    result,
                    {"path_ok": True, "path_error": None, "holds": expected},
                )
                self.assertEqual(result, run_trace_check(G1, formula, [], ["s0"]))

    def test_oversized_trace_raises(self):
        with self.assertRaises(ValueError) as cm:
            run_trace_check(G1, "G p", ["s0"] * 60, ["s0"])
        self.assertIn("60", str(cm.exception))

    def test_formula_validated_before_path(self):
        with self.assertRaises(ValueError):
            run_trace_check(G2, "((", ["s0"], ["s9"])
