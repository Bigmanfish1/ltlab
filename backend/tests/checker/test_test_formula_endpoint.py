import json

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from apps.accounts.models import Profile
from apps.exercises.views import test_formula

G1 = {
    "elements": {
        "nodes": [{"data": {"id": "s0", "name": "s0", "props": ["p"], "initial": True}}],
        "edges": [{"data": {"id": "e0", "source": "s0", "target": "s0"}}],
    }
}


class TestFormulaEndpointTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.student = Profile.objects.create(email="s@x.com", name="S", role=Profile.ROLE_STUDENT)

    def _post(self, profile, body):
        if not isinstance(body, str):
            body = json.dumps(body)
        request = self.factory.post(
            "/teacher/exercises/test-formula/", data=body, content_type="application/json"
        )
        request.profile = profile
        request.supabase_user = (
            type("U", (), {"id": "u", "email": getattr(profile, "email", "")})()
            if profile
            else None
        )
        request.session = SessionStore()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _call(self, profile, body):
        return test_formula(self._post(profile, body))

    def _json(self, response):
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def _payload(self, formula, mode, graph=G1):
        return {"graph": graph, "formula": formula, "mode": mode}

    def test_satisfiable_formula(self):
        body = self._json(self._call(self.teacher, self._payload("G p", "satisfiable")))
        self.assertEqual(body, {"ok": True, "result": "satisfiable"})

    def test_unsatisfiable_formula(self):
        body = self._json(self._call(self.teacher, self._payload("F !p", "satisfiable")))
        self.assertEqual(body, {"ok": True, "result": "unsatisfiable"})

    def test_undeclared_proposition_rejected(self):
        body = self._json(self._call(self.teacher, self._payload("F q", "satisfiable")))
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])
        self.assertIn("q", body["error"])

    def test_holds_mode_holds(self):
        body = self._json(self._call(self.teacher, self._payload("G p", "holds")))
        self.assertEqual(body, {"ok": True, "result": "holds"})

    def test_holds_mode_violated(self):
        body = self._json(self._call(self.teacher, self._payload("G !p", "holds")))
        self.assertEqual(body, {"ok": True, "result": "violated"})

    def test_invalid_formula_error(self):
        body = self._json(self._call(self.teacher, self._payload("((p", "satisfiable")))
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])

    def test_missing_formula_error(self):
        body = self._json(self._call(self.teacher, {"graph": G1, "mode": "satisfiable"}))
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])

    def test_empty_formula_error(self):
        body = self._json(self._call(self.teacher, self._payload("", "satisfiable")))
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])

    def test_missing_graph_error(self):
        body = self._json(
            self._call(self.teacher, {"formula": "G p", "mode": "satisfiable"})
        )
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])

    def test_graph_not_a_dict_error(self):
        body = self._json(
            self._call(self.teacher, {"graph": [1, 2], "formula": "G p", "mode": "holds"})
        )
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])

    def test_unknown_mode_error(self):
        body = self._json(self._call(self.teacher, self._payload("G p", "x")))
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])

    def test_malformed_json_body_error(self):
        body = self._json(self._call(self.teacher, "{not json"))
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])

    def test_student_blocked(self):
        response = self._call(self.student, self._payload("G p", "satisfiable"))
        if response.status_code == 200:
            body = json.loads(response.content)
            self.assertNotEqual(body.get("ok"), True)
        else:
            self.assertIn(response.status_code, (301, 302, 303, 403))
