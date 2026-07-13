import json

from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase, override_settings

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

from apps.accounts.models import Profile
from apps.exercises import views
from apps.exercises.models import Exercise, ExercisePart, Topic

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
    def test_publish_with_graph_saves_published(self):
        # grading is by model-checking against the graph, so publishing needs a
        # graph but no solution formula
        data = self._form(action="publish")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        ex = Exercise.objects.get(title="New Ex")
        self.assertTrue(ex.is_published)
        self.assertEqual(ex.kripke_structure["elements"]["nodes"][0]["data"]["id"], "idle")

    def test_publish_without_graph_rejected(self):
        data = self._form(action="publish", graph_data="")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_draft_saves_unpublished(self):
        data = self._form(action="draft", title="Draft Ex")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        ex = Exercise.objects.get(title="Draft Ex")
        self.assertFalse(ex.is_published)

    def test_no_operators_warns_teacher(self):
        request = self._req("post", self.teacher, self._form(action="publish", allowed_operators="[]"))
        views.exercise_builder(request)
        warnings = [m.message for m in get_messages(request) if m.level == messages.WARNING]
        self.assertTrue(any("No operators" in m for m in warnings))

    def test_missing_required_fields_rejected(self):
        data = self._form(action="publish", title="")
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

    def test_parts_prefilled_on_edit(self):
        ex = Exercise.objects.create(
            topic=self.topic, title="Eng", description="d", difficulty="beginner",
            hint="", exercise_type="english_to_formula", declared_aps=["p"],
        )
        part = ExercisePart.objects.create(exercise=ex, prompt="always p", formula="G p")
        context = views._builder_context(ex)
        self.assertEqual(context["exercise_type"], "english_to_formula")
        self.assertEqual(json.loads(context["declared_aps_json"]), ["p"])
        self.assertEqual(
            json.loads(context["parts_json"]),
            [{"id": str(part.id), "prompt": "always p", "formula": "G p"}],
        )


COFFEE_APS = '["coffee_chosen", "tea_chosen", "money_inserted", "coffee_delivered", "tea_delivered"]'
COFFEE_PARTS = json.dumps([
    {"prompt": "once in a while someone chooses tea or coffee",
     "formula": "G F (tea_chosen | coffee_chosen)"},
    {"prompt": "if coffee is chosen and next money is inserted coffee will be delivered",
     "formula": "G ((coffee_chosen & X money_inserted) -> F coffee_delivered)"},
    {"prompt": "when coffee is chosen tea will not be delivered until tea is chosen",
     "formula": "G (coffee_chosen -> (!tea_delivered U tea_chosen))"},
])


@override_settings(STORAGES=PLAIN_STATIC)
class EnglishBuilderTests(TeacherViewTestCase):
    def _english_form(self, **overrides):
        data = self._form(
            exercise_type="english_to_formula", graph_data="",
            declared_aps=COFFEE_APS, parts=COFFEE_PARTS,
        )
        data.update(overrides)
        return data

    def test_publish_english_creates_parts(self):
        data = self._english_form(action="publish")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        ex = Exercise.objects.get(title="New Ex")
        self.assertTrue(ex.is_published)
        self.assertEqual(ex.exercise_type, "english_to_formula")
        self.assertEqual(len(ex.declared_aps), 5)
        formulas = list(ex.parts.values_list("formula", flat=True))
        self.assertEqual(len(formulas), 3)
        self.assertIn("G F (tea_chosen | coffee_chosen)", formulas)

    def test_publish_english_without_aps_rejected(self):
        data = self._english_form(action="publish", declared_aps="[]")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_publish_english_without_parts_rejected(self):
        data = self._english_form(action="publish", parts="[]")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_publish_unparseable_target_rejected(self):
        parts = json.dumps([{"prompt": "p", "formula": "G (coffee_chosen"}])
        data = self._english_form(action="publish", parts=parts)
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_publish_target_with_undeclared_ap_rejected(self):
        parts = json.dumps([{"prompt": "p", "formula": "G espresso"}])
        data = self._english_form(action="publish", parts=parts)
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_publish_reserved_ap_name_rejected(self):
        data = self._english_form(action="publish", declared_aps='["G", "p"]')
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_draft_english_saves_without_validation(self):
        data = self._english_form(action="draft", declared_aps="[]", parts="[]")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Exercise.objects.get(title="New Ex").is_published)

    def test_no_graph_needed_for_english_publish(self):
        data = self._english_form(action="publish", graph_data="")
        views.exercise_builder(self._req("post", self.teacher, data))
        self.assertTrue(Exercise.objects.get(title="New Ex").is_published)

    def test_edit_syncs_parts_by_id(self):
        views.exercise_builder(self._req("post", self.teacher, self._english_form(action="publish")))
        ex = Exercise.objects.get(title="New Ex")
        kept, dropped, _ = list(ex.parts.all())
        edited = json.dumps([
            {"id": str(kept.id), "prompt": "edited prompt", "formula": kept.formula},
            {"prompt": "brand new", "formula": "F coffee_delivered"},
        ])
        data = self._english_form(action="publish", parts=edited)
        views.exercise_builder(self._req("post", self.teacher, data), ex.id)
        parts = list(ex.parts.all())
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].id, kept.id)
        self.assertEqual(parts[0].prompt, "edited prompt")
        self.assertEqual(parts[1].prompt, "brand new")
        self.assertFalse(ex.parts.filter(id=dropped.id).exists())

    def test_type_locked_on_edit(self):
        views.exercise_builder(self._req("post", self.teacher, self._english_form(action="publish")))
        ex = Exercise.objects.get(title="New Ex")
        data = self._english_form(action="publish", exercise_type="model_check")
        views.exercise_builder(self._req("post", self.teacher, data), ex.id)
        ex.refresh_from_db()
        self.assertEqual(ex.exercise_type, "english_to_formula")


# on REQGRANT the only infinite path is (idle req grant)^ω, so grant occurs
# infinitely often: F grant / G F idle have satisfying paths, G !grant has none
PATH_PARTS = json.dumps([
    {"prompt": "", "formula": "F grant"},
    {"prompt": "", "formula": "G F idle"},
])


@override_settings(STORAGES=PLAIN_STATIC)
class PathExhibitBuilderTests(TeacherViewTestCase):
    def _path_form(self, **overrides):
        data = self._form(exercise_type="path_exhibit", parts=PATH_PARTS)
        data.update(overrides)
        return data

    def test_publish_with_satisfiable_formulas_creates_parts(self):
        data = self._path_form(action="publish")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        ex = Exercise.objects.get(title="New Ex")
        self.assertTrue(ex.is_published)
        self.assertEqual(ex.exercise_type, "path_exhibit")
        self.assertEqual(ex.kripke_structure["elements"]["nodes"][0]["data"]["id"], "idle")
        self.assertEqual(list(ex.parts.values_list("formula", flat=True)), ["F grant", "G F idle"])

    def test_publish_with_unsatisfiable_formula_rejected(self):
        parts = json.dumps([
            {"prompt": "", "formula": "F grant"},
            {"prompt": "", "formula": "G (!grant)"},
        ])
        data = self._path_form(action="publish", parts=parts)
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_publish_with_unparseable_formula_rejected(self):
        parts = json.dumps([{"prompt": "", "formula": "F (grant"}])
        data = self._path_form(action="publish", parts=parts)
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_publish_without_parts_rejected(self):
        data = self._path_form(action="publish", parts="[]")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_publish_without_graph_rejected(self):
        data = self._path_form(action="publish", graph_data="")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_draft_skips_satisfiability_gate(self):
        parts = json.dumps([{"prompt": "", "formula": "G (!grant)"}])
        data = self._path_form(action="draft", parts=parts)
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Exercise.objects.get(title="New Ex").is_published)

    def test_type_locked_on_edit(self):
        views.exercise_builder(self._req("post", self.teacher, self._path_form(action="publish")))
        ex = Exercise.objects.get(title="New Ex")
        data = self._path_form(action="publish", exercise_type="english_to_formula")
        views.exercise_builder(self._req("post", self.teacher, data), ex.id)
        ex.refresh_from_db()
        self.assertEqual(ex.exercise_type, "path_exhibit")
