import json

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

# the canvas loads js/kripke_editor.js via {% static %}; the manifest storage
# has no manifest under test, so fall back to plain static for full renders
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

from apps.accounts.models import Profile
from apps.exercises import views
from apps.exercises.constants import BUILDER_OPERATORS
from apps.exercises.models import Attempt, Exercise, ExercisePart, Topic
from apps.exercises.services import solved_exercise_ids


def triggers(response):
    """The response's HTMX triggers as a dict — the header carries several."""
    raw = response.get("HX-Trigger")
    return json.loads(raw) if raw else {}

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

    def test_overlong_formula_rejected(self):
        response = views.submit_formula(self._post({"formula": "a" * 513}), self.published.id)
        self.assertContains(response, "too long")
        self.assertFalse(Attempt.objects.filter(exercise=self.published).exists())

    def test_correct_submission_signals_completion(self):
        response = views.submit_formula(self._post({"formula": "true"}), self.published.id)
        self.assertIn("exerciseSolved", triggers(response))

    def test_wrong_submission_does_not_signal_completion(self):
        response = views.submit_formula(self._post({"formula": "G a"}), self.published.id)
        self.assertNotIn("exerciseSolved", triggers(response))

    def test_grading_verdict_rides_the_trigger(self):
        correct = views.submit_formula(self._post({"formula": "true"}), self.published.id)
        self.assertTrue(triggers(correct)["answerGraded"]["correct"])
        wrong = views.submit_formula(self._post({"formula": "G a"}), self.published.id)
        self.assertFalse(triggers(wrong)["answerGraded"]["correct"])

    def test_rejected_submission_sounds_like_a_wrong_answer(self):
        response = views.submit_formula(self._post({"formula": "G ("}), self.published.id)
        self.assertFalse(triggers(response)["answerGraded"]["correct"])

    def test_get_hint_route_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("get_hint", args=[self.published.id])

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
        self.assertIn('data-insert="G"', html)
        self.assertIn('data-insert="F"', html)
        self.assertNotIn('data-insert="U"', html)
        self.assertNotIn('data-insert="¬"', html)  # ¬ hidden


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


@override_settings(STORAGES=PLAIN_STATIC)
class EnglishExerciseTests(StudentViewTestCase):
    def setUp(self):
        super().setUp()
        self.english = Exercise.objects.create(
            topic=self.topic, title="ZZENGLISH", description="coffee machine",
            difficulty="beginner", hint="", is_published=True, hints=[],
            exercise_type="english_to_formula",
            declared_aps=["coffee_chosen", "tea_chosen"],
            allowed_operators=list(BUILDER_OPERATORS),
        )
        self.part1 = ExercisePart.objects.create(
            exercise=self.english, position=0,
            prompt="once in a while someone chooses tea or coffee",
            formula="G F (tea_chosen | coffee_chosen)",
        )
        self.part2 = ExercisePart.objects.create(
            exercise=self.english, position=1,
            prompt="eventually coffee is chosen",
            formula="F coffee_chosen",
        )

    def _submit_part(self, part, formula):
        return views.submit_part(
            self._post({"formula": formula}), self.english.id, part.id
        )

    def test_canvas_renders_english_template(self):
        response = views.exercise_canvas(self._get(), self.english.id)
        self.assertContains(response, "REQUIREMENTS → LTL")
        self.assertContains(response, "once in a while someone chooses tea or coffee")
        self.assertContains(response, "coffee_chosen")

    def test_equivalent_submission_correct(self):
        # textually different but language-equivalent to the target
        response = self._submit_part(self.part2, "true U coffee_chosen")
        self.assertContains(response, "CORRECT")
        attempt = Attempt.objects.get(part=self.part2)
        self.assertTrue(attempt.is_correct)
        self.assertEqual(attempt.formula_input, "true U coffee_chosen")

    def test_wrong_submission_incorrect_without_target_leak(self):
        response = self._submit_part(self.part1, "F (tea_chosen | coffee_chosen)")
        self.assertContains(response, "INCORRECT")
        self.assertNotContains(response, "G F (tea_chosen | coffee_chosen)")
        self.assertFalse(Attempt.objects.get(part=self.part1).is_correct)

    def test_undeclared_ap_no_attempt(self):
        response = self._submit_part(self.part2, "F espresso")
        self.assertContains(response, "not in this exercise")
        self.assertFalse(Attempt.objects.filter(part=self.part2).exists())

    def test_solving_all_parts_triggers_completion(self):
        first = self._submit_part(self.part1, "G F (tea_chosen | coffee_chosen)")
        self.assertNotIn("exerciseSolved", triggers(first))
        second = self._submit_part(self.part2, "F coffee_chosen")
        self.assertIn("exerciseSolved", triggers(second))
        self.assertIn(self.english.id, solved_exercise_ids(self.student))

    def test_partial_parts_not_solved(self):
        self._submit_part(self.part1, "G F (tea_chosen | coffee_chosen)")
        self.assertNotIn(self.english.id, solved_exercise_ids(self.student))
        response = views.exercises(self._get())
        self.assertContains(response, "ZZENGLISH")

    def test_partless_completion_rule_unchanged(self):
        Attempt.objects.create(
            exercise=self.published, student=self.student, is_correct=True,
            formula_input="true",
        )
        self.assertIn(self.published.id, solved_exercise_ids(self.student))

    def test_part_submit_404_for_wrong_exercise(self):
        with self.assertRaises(Http404):
            views.submit_part(
                self._post({"formula": "F coffee_chosen"}),
                self.published.id, self.part1.id,
            )


@override_settings(STORAGES=PLAIN_STATIC)
class PathExhibitExerciseTests(StudentViewTestCase):
    # GRAPH is deterministic (one outgoing edge per state), so every valid
    # path from the initial state is the single run s0 s1 s0 s1 ... with
    # word ({a}{b})^omega — formulas below are chosen against that word
    def setUp(self):
        super().setUp()
        self.path = Exercise.objects.create(
            topic=self.topic, title="ZZPATH", description="alternator",
            difficulty="beginner", hint="", is_published=True, hints=[],
            exercise_type="path_exhibit", kripke_structure=GRAPH,
        )
        # G F b: b holds at every odd position of ({a}{b})^omega, hence
        # infinitely often — true on the unique valid run
        self.part_a = ExercisePart.objects.create(
            exercise=self.path, position=0, formula="G F b",
        )
        # X b: position 1 of ({a}{b})^omega is {b} — true on the unique run
        self.part_b = ExercisePart.objects.create(
            exercise=self.path, position=1, formula="X b",
        )

    def _submit_trace(self, part, prefix, cycle):
        return views.submit_part(
            self._post({"trace_prefix": prefix, "trace_cycle": cycle}),
            self.path.id, part.id,
        )

    def test_canvas_renders_path_template(self):
        response = views.exercise_canvas(self._get(), self.path.id)
        self.assertContains(response, "∃ MODEL CHECKING")
        self.assertContains(response, "G F b")
        self.assertContains(response, "X b")
        self.assertContains(response, "trace_prefix")
        self.assertContains(response, "trace_cycle")

    def test_satisfying_path_correct_with_full_attempt_shape(self):
        # prefix [] cycle [s0,s1] = ({a}{b})^omega satisfies G F b
        response = self._submit_trace(self.part_a, "[]", '["s0", "s1"]')
        self.assertContains(response, "CORRECT")
        self.assertNotContains(response, "INCORRECT")
        attempt = Attempt.objects.get(part=self.part_a)
        self.assertTrue(attempt.is_correct)
        self.assertIsNone(attempt.formula_input)
        self.assertEqual(attempt.answer, {"prefix": [], "cycle": ["s0", "s1"]})
        self.assertIsNone(attempt.misconception)

    def test_valid_but_unsatisfying_path_incorrect(self):
        # the only run the graph admits starts at s0 = {a}, so G b is false
        # at position 0 on every valid lasso
        self.part_b.formula = "G b"
        self.part_b.save(update_fields=["formula"])
        response = self._submit_trace(self.part_b, "[]", '["s0", "s1"]')
        self.assertContains(response, "INCORRECT")
        attempt = Attempt.objects.get(part=self.part_b)
        self.assertFalse(attempt.is_correct)
        self.assertEqual(attempt.answer, {"prefix": [], "cycle": ["s0", "s1"]})
        self.assertIsNone(attempt.misconception)

    def test_nonedge_step_incorrect_and_recorded(self):
        # cycle [s0] closes with s0 -> s0, an edge the graph does not have
        response = self._submit_trace(self.part_a, "[]", '["s0"]')
        self.assertContains(response, "INCORRECT")
        self.assertFalse(Attempt.objects.get(part=self.part_a).is_correct)

    def test_wrong_start_state_incorrect_and_recorded(self):
        # s1 s0 s1 s0 ... uses only real edges and its word {b}{a}... even
        # satisfies G F b — rejection can only come from the initial-state check
        response = self._submit_trace(self.part_a, '["s1"]', '["s0", "s1"]')
        self.assertContains(response, "INCORRECT")
        self.assertFalse(Attempt.objects.get(part=self.part_a).is_correct)

    def test_malformed_trace_json_no_attempt(self):
        response = self._submit_trace(self.part_a, "not json", '["s0", "s1"]')
        self.assertContains(response, "ERROR")
        self.assertFalse(Attempt.objects.filter(exercise=self.path).exists())

    def test_empty_cycle_no_attempt(self):
        response = self._submit_trace(self.part_a, '["s0"]', "[]")
        self.assertContains(response, "ERROR")
        self.assertFalse(Attempt.objects.filter(exercise=self.path).exists())

    def test_solving_all_parts_triggers_completion(self):
        first = self._submit_trace(self.part_a, "[]", '["s0", "s1"]')
        self.assertNotIn("exerciseSolved", triggers(first))
        # prefix [s0] cycle [s1,s0] is the same word ({a}{b})^omega: position 1
        # is {b}, so X b holds; edges s0->s1, s1->s0 and wrap s0->s1 all exist
        second = self._submit_trace(self.part_b, '["s0"]', '["s1", "s0"]')
        self.assertIn("exerciseSolved", triggers(second))
        self.assertIn(self.path.id, solved_exercise_ids(self.student))

    def test_submit_404_for_draft_path_exercise(self):
        draft_path = Exercise.objects.create(
            topic=self.topic, title="ZZPATHDRAFT", description="d",
            difficulty="beginner", hint="", is_published=False, hints=[],
            exercise_type="path_exhibit", kripke_structure=GRAPH,
        )
        part = ExercisePart.objects.create(
            exercise=draft_path, position=0, formula="G F b",
        )
        with self.assertRaises(Http404):
            views.submit_part(
                self._post({"trace_prefix": "[]", "trace_cycle": '["s0", "s1"]'}),
                draft_path.id, part.id,
            )
        self.assertFalse(Attempt.objects.filter(exercise=draft_path).exists())

    def test_part_from_other_exercise_404(self):
        foreign_part = ExercisePart.objects.create(
            exercise=self.draft, position=0, formula="G F b",
        )
        with self.assertRaises(Http404):
            self._submit_trace(foreign_part, "[]", '["s0", "s1"]')
        self.assertFalse(Attempt.objects.filter(exercise=self.path).exists())


@override_settings(STORAGES=PLAIN_STATIC)
class JudgeExerciseTests(StudentViewTestCase):
    # GRAPH admits the single run s0 s1 s0 s1 ... (word ({a}{b})^omega), so
    # "holds universally" is decided by that one word:
    #   G F b — b at every odd position, infinitely often -> HOLDS
    #   G a   — position 1 is {b}, a false there          -> VIOLATED
    def setUp(self):
        super().setUp()
        self.judge = Exercise.objects.create(
            topic=self.topic, title="ZZJUDGE", description="alternator",
            difficulty="beginner", hint="", is_published=True, hints=[],
            exercise_type="judge", kripke_structure=GRAPH,
        )
        self.part_true = ExercisePart.objects.create(
            exercise=self.judge, position=0, formula="G F b",
        )
        self.part_false = ExercisePart.objects.create(
            exercise=self.judge, position=1, formula="G a",
        )

    def _submit_verdict(self, part, data):
        return views.submit_part(self._post(data), self.judge.id, part.id)

    def test_canvas_renders_judge_template(self):
        response = views.exercise_canvas(self._get(), self.judge.id)
        self.assertContains(response, "∀ MODEL CHECKING")
        self.assertContains(response, "G F b")
        self.assertContains(response, "G a")
        self.assertContains(response, "trace_prefix")
        self.assertContains(response, "trace_cycle")

    def test_holds_claim_on_true_formula_correct(self):
        response = self._submit_verdict(self.part_true, {"verdict": "holds"})
        self.assertContains(response, "CORRECT")
        self.assertNotContains(response, "INCORRECT")
        attempt = Attempt.objects.get(part=self.part_true)
        self.assertTrue(attempt.is_correct)
        self.assertEqual(attempt.answer, {"verdict": "holds"})
        self.assertIsNone(attempt.formula_input)
        self.assertIsNone(attempt.misconception)

    def test_holds_claim_on_false_formula_incorrect(self):
        response = self._submit_verdict(self.part_false, {"verdict": "holds"})
        self.assertContains(response, "INCORRECT")
        attempt = Attempt.objects.get(part=self.part_false)
        self.assertFalse(attempt.is_correct)
        self.assertEqual(attempt.answer, {"verdict": "holds"})

    def test_violated_claim_with_witness_correct(self):
        # []·[s0,s1]^omega is the graph's run; G a fails there at position 1
        response = self._submit_verdict(
            self.part_false,
            {"verdict": "violated", "trace_prefix": "[]", "trace_cycle": '["s0", "s1"]'},
        )
        self.assertContains(response, "CORRECT")
        self.assertNotContains(response, "INCORRECT")
        attempt = Attempt.objects.get(part=self.part_false)
        self.assertTrue(attempt.is_correct)
        self.assertEqual(
            attempt.answer,
            {"verdict": "violated", "prefix": [], "cycle": ["s0", "s1"]},
        )
        self.assertIsNone(attempt.formula_input)
        self.assertIsNone(attempt.misconception)

    def test_violated_claim_on_true_formula_incorrect(self):
        # the lasso is a real path, but G F b holds on it (and on every path),
        # so no counterexample exists and the claim itself is wrong
        response = self._submit_verdict(
            self.part_true,
            {"verdict": "violated", "trace_prefix": "[]", "trace_cycle": '["s0", "s1"]'},
        )
        self.assertContains(response, "INCORRECT")
        attempt = Attempt.objects.get(part=self.part_true)
        self.assertFalse(attempt.is_correct)
        self.assertEqual(
            attempt.answer,
            {"verdict": "violated", "prefix": [], "cycle": ["s0", "s1"]},
        )

    def test_violated_claim_with_broken_path_incorrect_but_recorded(self):
        # cycle [s0] needs the edge s0 -> s0, which the graph does not have;
        # the verdict is right (G a is violated) but the witness is not a path
        response = self._submit_verdict(
            self.part_false,
            {"verdict": "violated", "trace_prefix": "[]", "trace_cycle": '["s0"]'},
        )
        self.assertContains(response, "INCORRECT")
        attempt = Attempt.objects.get(part=self.part_false)
        self.assertFalse(attempt.is_correct)
        self.assertEqual(
            attempt.answer,
            {"verdict": "violated", "prefix": [], "cycle": ["s0"]},
        )

    def test_missing_or_bogus_verdict_no_attempt(self):
        response = self._submit_verdict(self.part_true, {})
        self.assertEqual(response.status_code, 200)
        response = self._submit_verdict(self.part_true, {"verdict": "maybe"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Attempt.objects.filter(exercise=self.judge).exists())

    def test_violated_with_bad_trace_no_attempt(self):
        response = self._submit_verdict(
            self.part_false,
            {"verdict": "violated", "trace_prefix": "not json", "trace_cycle": '["s0", "s1"]'},
        )
        self.assertContains(response, "ERROR")
        response = self._submit_verdict(
            self.part_false,
            {"verdict": "violated", "trace_prefix": '["s0"]', "trace_cycle": "[]"},
        )
        self.assertContains(response, "ERROR")
        self.assertFalse(Attempt.objects.filter(exercise=self.judge).exists())

    def test_solving_all_parts_triggers_completion(self):
        first = self._submit_verdict(self.part_true, {"verdict": "holds"})
        self.assertNotIn("exerciseSolved", triggers(first))
        second = self._submit_verdict(
            self.part_false,
            {"verdict": "violated", "trace_prefix": "[]", "trace_cycle": '["s0", "s1"]'},
        )
        self.assertIn("exerciseSolved", triggers(second))
        self.assertIn(self.judge.id, solved_exercise_ids(self.student))
