import json

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase, override_settings

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

from apps.accounts.models import Profile
from apps.exercises import views
from apps.exercises.models import Exercise, Topic

REQGRANT = {
    "elements": {
        "nodes": [
            {"data": {"id": "idle", "name": "idle", "label": "idle", "props": ["idle"], "initial": True}},
            {"data": {"id": "req", "name": "req", "label": "req", "props": ["req"], "initial": False}},
            {"data": {"id": "grant", "name": "grant", "label": "grant", "props": ["grant"], "initial": False}},
        ],
        "edges": [
            {"data": {"id": "e0", "source": "idle", "target": "req"}},
            {"data": {"id": "e1", "source": "req", "target": "grant"}},
            {"data": {"id": "e2", "source": "grant", "target": "idle"}},
        ],
    }
}


class TeacherViewTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.student = Profile.objects.create(email="s@x.com", name="S", role=Profile.ROLE_STUDENT)
        self.topic = Topic.objects.create(title="LTL", created_by=self.teacher)

    def _req(self, method, profile, data=None):
        request = getattr(self.factory, method)("/", data or {})
        request.profile = profile
        request.supabase_user = type("U", (), {"id": "u", "email": getattr(profile, "email", "")})() if profile else None
        request.session = SessionStore()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _form(self, **overrides):
        data = {
            "title": "New Ex",
            "description": "desc",
            "difficulty": "intermediate",
            "topic": str(self.topic.id),
            "graph_data": json.dumps(REQGRANT),
            "hint_1": "hint",
            "allowed_operators": '["G", "F"]',
        }
        data.update(overrides)
        return data


class GatingTests(TeacherViewTestCase):
    def test_student_blocked_from_manage(self):
        response = views.manage(self._req("get", self.student))
        self.assertEqual(response.status_code, 302)

    def test_teacher_allowed(self):
        response = views.manage(self._req("get", self.teacher))
        self.assertEqual(response.status_code, 200)


@override_settings(STORAGES=PLAIN_STATIC)
class PublishValidationTests(TeacherViewTestCase):
    def test_publish_holding_formula_saves_published(self):
        data = self._form(action="publish", formula="G (req -> F grant)")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        ex = Exercise.objects.get(title="New Ex")
        self.assertTrue(ex.is_published)
        self.assertEqual(ex.kripke_structure["elements"]["nodes"][0]["data"]["id"], "idle")

    def test_publish_violated_formula_rejected(self):
        data = self._form(action="publish", formula="G grant")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_draft_skips_holds_check(self):
        data = self._form(action="draft", title="Draft Ex", formula="nonsense formula")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        ex = Exercise.objects.get(title="Draft Ex")
        self.assertFalse(ex.is_published)

    def test_missing_required_fields_rejected(self):
        data = self._form(action="publish", formula="G (req -> F grant)", title="")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(description="desc", title="").exists())


class CrudTests(TeacherViewTestCase):
    def test_topic_create_and_delete(self):
        views.topic_create(self._req("post", self.teacher, {"title": "M", "visible": "1"}))
        topic = Topic.objects.get(title="M")
        self.assertTrue(topic.visible)
        views.topic_delete(self._req("post", self.teacher), topic.id)
        self.assertFalse(Topic.objects.filter(title="M").exists())

    def test_topic_create_positions_after_max(self):
        Topic.objects.create(title="High", created_by=self.teacher, position=5)
        views.topic_create(self._req("post", self.teacher, {"title": "M", "visible": "1"}))
        self.assertEqual(Topic.objects.get(title="M").position, 6)

    def test_duplicate_title_rejected_case_insensitively(self):
        before = Topic.objects.count()
        views.topic_create(self._req("post", self.teacher, {"title": "ltl", "visible": "1"}))
        self.assertEqual(Topic.objects.count(), before)

    def test_topic_update_edits_fields(self):
        topic = Topic.objects.create(title="Old", created_by=self.teacher, visible=True)
        views.topic_update(
            self._req("post", self.teacher, {"title": "New", "description": "d", "visible": "0"}),
            topic.id,
        )
        topic.refresh_from_db()
        self.assertEqual(topic.title, "New")
        self.assertFalse(topic.visible)

    def test_topic_update_rejects_duplicate_title(self):
        topic = Topic.objects.create(title="Other", created_by=self.teacher)
        views.topic_update(self._req("post", self.teacher, {"title": "ltl", "visible": "1"}), topic.id)
        topic.refresh_from_db()
        self.assertEqual(topic.title, "Other")

    def test_topic_visibility_toggles(self):
        topic = Topic.objects.create(title="V", created_by=self.teacher, visible=True)
        views.topic_visibility(self._req("post", self.teacher), topic.id)
        topic.refresh_from_db()
        self.assertFalse(topic.visible)

    def test_exercise_delete(self):
        ex = Exercise.objects.create(
            topic=self.topic, title="Del", description="d", difficulty="beginner",
            hint="h", target_formula="G p",
        )
        views.exercise_delete(self._req("post", self.teacher), ex.id)
        self.assertFalse(Exercise.objects.filter(pk=ex.id).exists())


class BuilderContextTests(TeacherViewTestCase):
    def test_empty_allow_list_is_preserved_on_edit(self):
        # teacher who disabled every operator stored [] — must not re-enable all
        ex = Exercise.objects.create(
            topic=self.topic, title="Locked", description="d", difficulty="beginner",
            hint="h", target_formula="G p", allowed_operators=[],
        )
        context = views._builder_context(ex)
        self.assertEqual(context["allowed_operators"], [])
