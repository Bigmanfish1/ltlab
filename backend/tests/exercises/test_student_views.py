from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

# the canvas loads js/kripke_editor.js via {% static %}; the manifest storage
# has no manifest under test, so fall back to plain static for full renders
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

from apps.accounts.models import Profile
from apps.exercises import views
from apps.exercises.constants import BUILDER_OPERATORS
from apps.exercises.models import Attempt, Exercise, Topic

# s0 --a--> s1 --b--> s0 ; "true" holds, "G a" is violated at s1
GRAPH = {
    "elements": {
        "nodes": [
            {"data": {"id": "s0", "name": "s0", "props": ["a"], "initial": True}},
            {"data": {"id": "s1", "name": "s1", "props": ["b"]}},
        ],
        "edges": [
            {"data": {"id": "e0", "source": "s0", "target": "s1"}},
            {"data": {"id": "e1", "source": "s1", "target": "s0"}},
        ],
    }
}


class StudentViewTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.student = Profile.objects.create(email="s@x.com", name="S", role=Profile.ROLE_STUDENT)
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.topic = Topic.objects.create(title="T", created_by=self.teacher)
        self.published = Exercise.objects.create(
            topic=self.topic, title="ZZPUBLISHED", description="d", difficulty="beginner",
            hint="", target_formula=None, is_published=True, hints=["h1", "h2"],
            kripke_structure=GRAPH, allowed_operators=list(BUILDER_OPERATORS),
        )
        self.draft = Exercise.objects.create(
            topic=self.topic, title="ZZDRAFT", description="d", difficulty="beginner",
            hint="", target_formula=None, is_published=False, kripke_structure=GRAPH,
        )

    def _post(self, data):
        request = self.factory.post("/", data)
        request.profile = self.student
        request.supabase_user = type("U", (), {"id": "u", "email": self.student.email})()
        request.session = SessionStore()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _get(self):
        request = self.factory.get("/")
        request.profile = self.student
        request.supabase_user = type("U", (), {"id": "u", "email": self.student.email})()
        return request


class DraftVisibilityTests(StudentViewTestCase):
    def test_list_excludes_drafts(self):
        response = views.exercises(self._get())
        self.assertContains(response, "ZZPUBLISHED")
        self.assertNotContains(response, "ZZDRAFT")

    def test_canvas_404_for_draft(self):
        with self.assertRaises(Http404):
            views.exercise_canvas(self._get(), self.draft.id)

    def test_submit_404_for_draft(self):
        with self.assertRaises(Http404):
            views.submit_formula(self._post({"formula": "true"}), self.draft.id)
        self.assertFalse(Attempt.objects.filter(exercise=self.draft).exists())


class GradingTests(StudentViewTestCase):
    def test_satisfied_formula_marks_correct(self):
        response = views.submit_formula(self._post({"formula": "true"}), self.published.id)
        self.assertContains(response, "Property holds")
        self.assertTrue(Attempt.objects.get(exercise=self.published).is_correct)

    def test_violated_formula_marks_incorrect(self):
        response = views.submit_formula(self._post({"formula": "G a"}), self.published.id)
        self.assertContains(response, "Property violated")
        self.assertFalse(Attempt.objects.get(exercise=self.published).is_correct)

    def test_invalid_formula_records_no_attempt(self):
        response = views.submit_formula(self._post({"formula": "G G G ("}), self.published.id)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Attempt.objects.filter(exercise=self.published).exists())

    def test_empty_formula_records_no_attempt(self):
        views.submit_formula(self._post({"formula": ""}), self.published.id)
        self.assertFalse(Attempt.objects.filter(exercise=self.published).exists())

    def test_hints_used_clamped_to_authored_count(self):
        # only two hints authored; a client claiming nine must be clamped
        views.submit_formula(self._post({"formula": "true", "hints_used": "9"}), self.published.id)
        self.assertEqual(Attempt.objects.get(exercise=self.published).hints_used, 2)


@override_settings(STORAGES=PLAIN_STATIC)
class OperatorPaletteTests(StudentViewTestCase):
    def test_palette_limited_to_allowed_operators(self):
        self.published.allowed_operators = ["G", "F"]
        self.published.save(update_fields=["allowed_operators"])
        html = views.exercise_canvas(self._get(), self.published.id).content.decode()
        self.assertIn("insertOp('G')", html)
        self.assertIn("insertOp('F')", html)
        self.assertNotIn("insertOp('U')", html)
        self.assertNotIn("insertOp('¬')", html)  # ¬ hidden


class OperatorEnforcementTests(StudentViewTestCase):
    def setUp(self):
        super().setUp()
        self.published.allowed_operators = ["G", "F"]
        self.published.save(update_fields=["allowed_operators"])

    def _submit(self, formula):
        return views.submit_formula(self._post({"formula": formula}), self.published.id)

    def test_allowed_operators_are_graded(self):
        response = self._submit("G F a")
        self.assertContains(response, "Property")  # graded (holds/violated)
        self.assertTrue(Attempt.objects.filter(exercise=self.published).exists())

    def test_disallowed_operator_rejected(self):
        response = self._submit("X a")
        self.assertContains(response, "allowed for this exercise")
        self.assertFalse(Attempt.objects.filter(exercise=self.published).exists())

    def test_until_rejected_when_not_allowed(self):
        response = self._submit("a U b")
        self.assertContains(response, "allowed for this exercise")
        self.assertFalse(Attempt.objects.filter(exercise=self.published).exists())

    def test_unsupported_operator_always_rejected(self):
        # R (release) has no builder button, so it can never be permitted
        response = self._submit("a R b")
        self.assertContains(response, "allowed for this exercise")
        self.assertFalse(Attempt.objects.filter(exercise=self.published).exists())
