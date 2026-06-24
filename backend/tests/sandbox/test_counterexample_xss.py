"""Regression test for the counterexample XSS fix.

Before the fix, graph_data / trace_json / violating_edges_json were echoed
verbatim into <script> blocks with |safe, allowing a state named
</script><script>alert(1)</script> to break out of the script context.

After the fix, the view passes Python objects to the template and the template
uses Django's json_script filter, which escapes <, >, &, U+2028 and U+2029.
"""

import json

from django.test import Client, TestCase
from django.urls import reverse


def _make_graph(state_name: str) -> str:
    """Build a minimal Cytoscape graph JSON with one state whose name contains
    the given potentially-hostile string."""
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


BREAKOUT_SEQUENCES = [
    "</script>",
    "</script><script>alert(1)</script>",
    "\\u2028",  # line separator — a JS line terminator
    "\\u2029",  # paragraph separator — another JS line terminator
]

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

    def _post_ce(self, formula="G p", **extra):
        """POST to the counterexample view with optional overrides."""
        data = {
            "formula": formula,
            "graph_data":             extra.get("graph_data",     _make_graph("s0")),
            "trace_json":             extra.get("trace_json",     INJECTION_TRACE),
            "violating_states_json":  extra.get("vst_json",       json.dumps(["s0"])),
            "violating_edges_json":   extra.get("vedges_json",    INJECTION_EDGES),
            "violating_subformula":   extra.get("vsubformula",    "p"),
            "violation_kind":         extra.get("vkind",          "safety"),
        }
        c = Client()
        c.force_login(
            # counterexample uses supabase_login_required; force_login bypasses it.
            type("User", (), {
                "is_authenticated": True,
                "pk": 1,
                "backend": "django.contrib.auth.backends.ModelBackend",
            })()
        )
        # The counterexample view requires a logged-in user from Supabase middleware;
        # patch it so we can test without a real DB.
        from unittest.mock import patch
        with patch("apps.checker.views.supabase_login_required", lambda f: f):
            return self.client.post(reverse("sandbox_counterexample"), data)

    def test_script_breakout_not_present_in_trace_json(self):
        """</script> injected via trace_json must not appear unescaped."""
        resp = self._post_ce(trace_json=INJECTION_TRACE)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn("</script><script>", content)

    def test_script_breakout_not_present_in_graph_data(self):
        hostile = _make_graph("</script><script>alert('xss')//")
        resp = self._post_ce(graph_data=hostile)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn("</script><script>", content)

    def test_script_breakout_not_present_in_violating_edges(self):
        resp = self._post_ce(vedges_json=INJECTION_EDGES)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn("</script><script>", content)

    def test_formula_xss_in_formula_field(self):
        """Hostile formula string must not break out of script context."""
        hostile_formula = "</script><script>alert(1)//"
        resp = self._post_ce(formula=hostile_formula)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn("</script><script>", content)

    def test_json_script_ids_present(self):
        """json_script filter must render the <script type=application/json> tags."""
        resp = self._post_ce()
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        for tag_id in ("__trace", "__graph", "__vedges", "__formula"):
            self.assertIn(f'id="{tag_id}"', content)
