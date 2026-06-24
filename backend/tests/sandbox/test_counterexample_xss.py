"""Regression test for the counterexample XSS fix.

Before the fix, graph_data / trace_json / violating_edges_json were echoed
verbatim into <script> blocks with |safe, allowing a state named
</script><script>alert(1)</script> to break out of the script context.

After the fix, the view passes Python objects to the template and the template
uses Django's json_script filter, which escapes <, >, &, U+2028 and U+2029.
"""

import json

from django.test import RequestFactory, TestCase
from django.urls import reverse


def _fake_user():
    """Minimal fake Supabase user dict (mirrors what the middleware sets)."""
    return {"id": "test-uid", "email": "test@example.com"}


def _make_graph(state_name: str) -> str:
    graph = {
        "elements": {
            "nodes": [
                {
                    "data": {
                        "id": "s0",
                        "name": state_name,
                        "label": state_name,
                        "initial": True,
                        "props": [],
                    }
                }
            ],
            "edges": [],
        }
    }
    return json.dumps(graph)


INJECTION_TRACE = json.dumps([
    {
        "state": "s0",
        "name": "</script><script>alert(1)//",
        "props": [],
        "status": "violating",
        "highlight": "",
        "reason": "",
        "in_cycle": True,
        "cycle_back": True,
    }
])

INJECTION_EDGES = json.dumps([["</script>", "s0"]])


class CounterexampleXSSRegressionTests(TestCase):
    """Verify that hostile strings in every POST field are safely escaped."""

    def _post_ce(self, formula="G p", **extra):
        from apps.checker.views import counterexample

        rf = RequestFactory()
        data = {
            "formula":              formula,
            "graph_data":           extra.get("graph_data",  _make_graph("s0")),
            "trace_json":           extra.get("trace_json",  INJECTION_TRACE),
            "violating_states_json": extra.get("vst_json",   json.dumps(["s0"])),
            "violating_edges_json": extra.get("vedges_json", INJECTION_EDGES),
            "violating_subformula": extra.get("vsubformula", "p"),
            "violation_kind":       extra.get("vkind",       "safety"),
        }
        req = rf.post("/sandbox/counterexample/", data)
        # supabase_login_required checks both attributes.
        req.supabase_user = _fake_user()
        req.profile       = object()  # non-None → passes the profile check
        return counterexample(req)

    def test_script_breakout_not_present_in_trace_json(self):
        resp = self._post_ce(trace_json=INJECTION_TRACE)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"</script><script>", resp.content)

    def test_script_breakout_not_present_in_graph_data(self):
        hostile = _make_graph("</script><script>alert('xss')//")
        resp = self._post_ce(graph_data=hostile)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"</script><script>", resp.content)

    def test_script_breakout_not_present_in_violating_edges(self):
        resp = self._post_ce(vedges_json=INJECTION_EDGES)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"</script><script>", resp.content)

    def test_formula_xss_in_formula_field(self):
        hostile_formula = "</script><script>alert(1)//"
        resp = self._post_ce(formula=hostile_formula)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"</script><script>", resp.content)

    def test_json_script_ids_present(self):
        resp = self._post_ce()
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        for tag_id in ("__trace", "__graph", "__vedges", "__formula"):
            self.assertIn(f'id="{tag_id}"', content)
