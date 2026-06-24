from django.test import TestCase

from apps.checker.views import MAX_EDGES, MAX_FORMULA_CHARS, MAX_NODES, _validate_graph, verify_ltl


# ── Helpers ───────────────────────────────────────────────────────────────────

def node(id, initial=False, props=None):
    return {"data": {"id": id, "initial": initial, "props": props or []}}


def edge(src, tgt):
    return {"data": {"id": f"e_{src}_{tgt}", "source": src, "target": tgt}}


# ── Duplicate transition tests ────────────────────────────────────────────────

class DuplicateTransitionValidationTests(TestCase):

    def test_valid_graph_with_no_duplicate_transitions(self):
        nodes = [node("s0", initial=True), node("s1")]
        edges = [edge("s0", "s1"), edge("s1", "s0")]
        self.assertIsNone(_validate_graph(nodes, edges))

    def test_blocks_duplicate_directed_edge(self):
        nodes = [node("s0", initial=True), node("s1")]
        edges = [edge("s0", "s1"), edge("s0", "s1")]
        error = _validate_graph(nodes, edges)
        self.assertIsNotNone(error)
        self.assertIn("Duplicate transition", error)

    def test_blocks_duplicate_self_loop(self):
        nodes = [node("s0", initial=True)]
        edges = [edge("s0", "s0"), edge("s0", "s0")]
        error = _validate_graph(nodes, edges)
        self.assertIsNotNone(error)
        self.assertIn("Duplicate transition", error)

    def test_allows_single_self_loop(self):
        nodes = [node("s0", initial=True)]
        edges = [edge("s0", "s0")]
        self.assertIsNone(_validate_graph(nodes, edges))

    def test_allows_reverse_edge(self):
        # s0 → s1 and s1 → s0 are two distinct transitions
        nodes = [node("s0", initial=True), node("s1")]
        edges = [edge("s0", "s1"), edge("s1", "s0")]
        self.assertIsNone(_validate_graph(nodes, edges))

    def test_no_edges_passes(self):
        nodes = [node("s0", initial=True)]
        self.assertIsNone(_validate_graph(nodes, []))

    def test_edges_none_skips_duplicate_check(self):
        # Backward-compat: callers that don't supply edges bypass the check
        nodes = [node("s0", initial=True)]
        self.assertIsNone(_validate_graph(nodes, None))

    def test_duplicate_in_larger_graph(self):
        # One duplicate pair among several valid edges
        nodes = [node("s0", initial=True), node("s1"), node("s2")]
        edges = [
            edge("s0", "s1"),
            edge("s1", "s2"),
            edge("s2", "s0"),
            edge("s1", "s2"),  # duplicate
        ]
        error = _validate_graph(nodes, edges)
        self.assertIsNotNone(error)
        self.assertIn("Duplicate transition", error)


# ── Pre-existing basic validation tests ──────────────────────────────────────

class BasicGraphValidationTests(TestCase):

    def test_empty_nodes_returns_error(self):
        self.assertIsNotNone(_validate_graph([]))

    def test_no_initial_state_returns_error(self):
        nodes = [node("s0", initial=False)]
        error = _validate_graph(nodes)
        self.assertIsNotNone(error)
        self.assertIn("initial", error.lower())

    def test_multiple_initial_states_returns_error(self):
        nodes = [node("s0", initial=True), node("s1", initial=True)]
        error = _validate_graph(nodes)
        self.assertIsNotNone(error)
        self.assertIn("Multiple", error)

    def test_single_initial_state_passes(self):
        nodes = [node("s0", initial=True), node("s1", initial=False)]
        self.assertIsNone(_validate_graph(nodes))


# ── Node/edge count cap tests ─────────────────────────────────────────────────

class GraphSizeCapTests(TestCase):

    def test_too_many_nodes_returns_error(self):
        nodes = [node(f"s{i}", initial=(i == 0)) for i in range(MAX_NODES + 1)]
        error = _validate_graph(nodes)
        self.assertIsNotNone(error)
        self.assertIn(str(MAX_NODES), error)

    def test_exactly_max_nodes_passes(self):
        nodes = [node(f"s{i}", initial=(i == 0)) for i in range(MAX_NODES)]
        self.assertIsNone(_validate_graph(nodes))

    def test_too_many_edges_returns_error(self):
        n = [node("s0", initial=True), node("s1")]
        # Build MAX_EDGES+1 *distinct* edge dicts (source/target vary to avoid
        # the duplicate-transition guard; we only test the count cap here).
        edges = []
        for i in range(MAX_EDGES + 1):
            edges.append({"data": {"id": f"e{i}", "source": f"x{i}", "target": f"y{i}"}})
        error = _validate_graph(n, edges)
        self.assertIsNotNone(error)
        self.assertIn(str(MAX_EDGES), error)

    def test_exactly_max_edges_passes(self):
        n = [node("s0", initial=True), node("s1")]
        edges = []
        for i in range(MAX_EDGES):
            edges.append({"data": {"id": f"e{i}", "source": f"x{i}", "target": f"y{i}"}})
        self.assertIsNone(_validate_graph(n, edges))


# ── Formula-length cap (view-level coarse filter) ─────────────────────────────

class FormulaLengthCapTests(TestCase):

    def _post_verify(self, formula, graph_json):
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.post("/sandbox/verify/", {"formula": formula, "graph_data": graph_json})
        req.user = type("User", (), {"is_authenticated": True})()
        # Bypass supabase_login_required: patch the decorator result
        from unittest.mock import patch
        with patch("apps.checker.views.run_ltl_check") as mock_task:
            mock_task.delay.return_value = type("R", (), {"id": "fake"})()
            response = verify_ltl(req)
        return response

    def test_oversized_formula_returns_error(self):
        import json
        from django.test import RequestFactory

        graph = {"elements": {"nodes": [{"data": {"id": "s0", "name": "s0", "initial": True, "props": []}}], "edges": []}}
        formula = "p " * (MAX_FORMULA_CHARS // 2 + 10)  # definitely over the char limit
        rf = RequestFactory()
        req = rf.post("/sandbox/verify/", {"formula": formula, "graph_data": json.dumps(graph)})
        # supabase_login_required checks both attributes.
        req.supabase_user = {"id": "test-uid", "email": "test@example.com"}
        req.profile       = object()
        response = verify_ltl(req)
        self.assertEqual(response.status_code, 200)
        self.assertIn("too long", response.content.decode())


# ── Proposition-name validation ───────────────────────────────────────────────

class PropNameValidationTests(TestCase):

    def test_valid_prop_names_pass(self):
        for name in ("p", "q", "req", "my_prop", "a1", "_ok"):
            nodes = [node("s0", initial=True, props=[name])]
            self.assertIsNone(_validate_graph(nodes), f"Expected {name!r} to be valid")

    def test_invalid_prop_starts_with_digit(self):
        nodes = [node("s0", initial=True, props=["1bad"])]
        error = _validate_graph(nodes)
        self.assertIsNotNone(error)
        self.assertIn("1bad", error)

    def test_invalid_prop_contains_space(self):
        nodes = [node("s0", initial=True, props=["bad prop"])]
        error = _validate_graph(nodes)
        self.assertIsNotNone(error)

    def test_invalid_prop_contains_dash(self):
        nodes = [node("s0", initial=True, props=["bad-prop"])]
        error = _validate_graph(nodes)
        self.assertIsNotNone(error)

    def test_reserved_operator_props_blocked(self):
        for reserved in ("X", "F", "G", "U", "R", "W", "M", "true", "false", "tt", "ff"):
            nodes = [node("s0", initial=True, props=[reserved])]
            error = _validate_graph(nodes)
            self.assertIsNotNone(error, f"Expected {reserved!r} to be blocked")
            self.assertIn("reserved", error)

    def test_multiple_props_first_invalid_reported(self):
        nodes = [node("s0", initial=True, props=["p", "G", "q"])]
        error = _validate_graph(nodes)
        self.assertIsNotNone(error)
        self.assertIn("G", error)
