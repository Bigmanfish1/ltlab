from django.test import SimpleTestCase

from apps.checker.tasks import run_trace_check

REQUEST_GRANT = {
    "elements": {
        "nodes": [
            {"data": {"id": "s0", "name": "s0", "props": ["idle"], "initial": True}},
            {"data": {"id": "s1", "name": "s1", "props": ["req"]}},
            {"data": {"id": "s2", "name": "s2", "props": ["req", "grant"]}},
        ],
        "edges": [
            {"data": {"id": "e0", "source": "s0", "target": "s0"}},
            {"data": {"id": "e1", "source": "s0", "target": "s1"}},
            {"data": {"id": "e2", "source": "s1", "target": "s2"}},
            {"data": {"id": "e3", "source": "s2", "target": "s0"}},
        ],
    }
}


class TraceCheckTestMixin:
    def _check(self, formula, prefix, cycle):
        return run_trace_check(REQUEST_GRANT, formula, prefix, cycle)

    def assert_path_invalid(self, result):
        self.assertFalse(result["path_ok"])
        self.assertTrue(result["path_error"])
        self.assertIsNone(result["holds"])


class PathValidityTests(TraceCheckTestMixin, SimpleTestCase):
    def test_valid_lasso_with_prefix(self):
        result = self._check("F grant", ["s0"], ["s1", "s2", "s0"])
        self.assertTrue(result["path_ok"])
        self.assertIsNone(result["path_error"])
        self.assertIsNotNone(result["holds"])

    def test_valid_empty_prefix_self_loop_lasso(self):
        result = self._check("G idle", [], ["s0"])
        self.assertTrue(result["path_ok"])
        self.assertIsNone(result["path_error"])
        self.assertIsNotNone(result["holds"])

    def test_non_edge_step_within_prefix_rejected(self):
        self.assert_path_invalid(self._check("G idle", ["s0", "s2"], ["s0"]))

    def test_non_edge_step_within_cycle_rejected(self):
        self.assert_path_invalid(self._check("G idle", ["s0"], ["s1", "s0"]))

    def test_wrong_start_state_rejected(self):
        self.assert_path_invalid(self._check("G idle", ["s1", "s2"], ["s0"]))

    def test_wrong_start_state_empty_prefix_rejected(self):
        self.assert_path_invalid(self._check("G idle", [], ["s1", "s2", "s0"]))

    def test_unknown_node_id_rejected(self):
        self.assert_path_invalid(self._check("G idle", ["s0"], ["s9"]))

    def test_empty_cycle_rejected(self):
        self.assert_path_invalid(self._check("G idle", ["s0"], []))

    def test_one_state_cycle_without_self_loop_rejected(self):
        self.assert_path_invalid(self._check("G idle", ["s0"], ["s1"]))


class FormulaEvaluationTests(TraceCheckTestMixin, SimpleTestCase):
    def test_g_idle_holds_on_idle_self_loop(self):
        # Word {idle}^w: idle holds at every position, so G idle is True.
        self.assertTrue(self._check("G idle", [], ["s0"])["holds"])

    def test_f_grant_fails_on_idle_self_loop(self):
        # Word {idle}^w: grant never appears, so F grant is False.
        self.assertFalse(self._check("F grant", [], ["s0"])["holds"])

    def test_f_grant_holds_on_request_cycle(self):
        # Word {idle} ({req} {req,grant} {idle})^w: grant holds at position 2,
        # so F grant is True.
        self.assertTrue(self._check("F grant", ["s0"], ["s1", "s2", "s0"])["holds"])

    def test_response_property_holds_on_request_cycle(self):
        # Word {idle} ({req} {req,grant} {idle})^w: grant recurs infinitely often
        # (positions 2, 5, 8, ...), so F grant holds at every position and the
        # implication req -> F grant is True everywhere; G (req -> F grant) is True.
        self.assertTrue(
            self._check("G (req -> F grant)", ["s0"], ["s1", "s2", "s0"])["holds"]
        )

    def test_x_req_holds_on_request_cycle(self):
        # Word {idle} ({req} {req,grant} {idle})^w: position 1 is s1 = {req},
        # so X req is True at position 0.
        self.assertTrue(self._check("X req", ["s0"], ["s1", "s2", "s0"])["holds"])

    def test_g_idle_fails_on_request_cycle(self):
        # Word {idle} ({req} {req,grant} {idle})^w: position 1 is {req} without
        # idle, so G idle is False.
        self.assertFalse(self._check("G idle", ["s0"], ["s1", "s2", "s0"])["holds"])

    def test_until_holds_when_leaving_s0_immediately(self):
        # Word {idle} ({req} {req,grant} {idle})^w: idle holds at position 0 and
        # req holds at position 1, so idle U req is True.
        self.assertTrue(self._check("idle U req", ["s0"], ["s1", "s2", "s0"])["holds"])

    def test_until_fails_when_looping_in_s0_forever(self):
        # Word {idle}^w: req never holds, and U requires its right operand to
        # eventually hold, so idle U req is False.
        self.assertFalse(self._check("idle U req", [], ["s0"])["holds"])

    def test_unicode_operators_accepted(self):
        # Same word and property as the response test above, written with
        # Unicode connectives: G (req -> F grant) is True.
        self.assertTrue(
            self._check("G (¬req ∨ F grant)", ["s0"], ["s1", "s2", "s0"])["holds"]
        )


class TraceValidationTests(TraceCheckTestMixin, SimpleTestCase):
    def test_unparseable_formula_raises(self):
        with self.assertRaises(ValueError):
            self._check("G (idle", [], ["s0"])

    def test_undeclared_ap_raises(self):
        with self.assertRaises(ValueError):
            self._check("F espresso", [], ["s0"])

    def test_temporal_operator_cap_raises(self):
        with self.assertRaises(ValueError):
            self._check("X " * 11 + "idle", [], ["s0"])

    def test_trace_longer_than_60_states_raises(self):
        with self.assertRaises(ValueError):
            self._check("G idle", ["s0"] * 61, ["s0"])
