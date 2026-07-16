from django.test import TestCase

from apps.accounts.models import Profile
from apps.checker.misconceptions import BUCKETS
from apps.exercises import services
from apps.exercises.constants import MISCONCEPTION_DESCRIPTIONS, MISCONCEPTION_LABELS
from apps.exercises.models import Attempt, Exercise, ExercisePart, Topic


# Misconception classification is tested directly in tests/checker/test_misconceptions.py.


class MisconceptionLabelSyncTests(TestCase):
    def test_labels_cover_every_bucket(self):
        self.assertEqual(set(MISCONCEPTION_LABELS), set(BUCKETS))

    def test_descriptions_cover_every_bucket(self):
        self.assertEqual(set(MISCONCEPTION_DESCRIPTIONS), set(BUCKETS))


class ReconciliationTests(TestCase):
    def setUp(self):
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.s1 = Profile.objects.create(email="s1@x.com", name="S One", role=Profile.ROLE_STUDENT)
        self.s2 = Profile.objects.create(email="s2@x.com", name="S Two", role=Profile.ROLE_STUDENT)
        self.topic = Topic.objects.create(title="T1", created_by=self.teacher)
        self.e1 = Exercise.objects.create(
            topic=self.topic, title="E1", description="d", difficulty="beginner",
            hint="h", target_formula="G F p", is_published=True,
        )
        self.e2 = Exercise.objects.create(
            topic=self.topic, title="E2", description="d", difficulty="beginner",
            hint="h", target_formula="G p", is_published=True,
        )
        Attempt.objects.create(exercise=self.e1, student=self.s1, is_correct=False, formula_input="F G p")
        Attempt.objects.create(exercise=self.e1, student=self.s1, is_correct=True, formula_input="G F p")
        Attempt.objects.create(exercise=self.e2, student=self.s1, is_correct=True, formula_input="G p")
        Attempt.objects.create(exercise=self.e1, student=self.s2, is_correct=False, formula_input="F p")

    def test_student_detail_matches_roster(self):
        roster = {r["id"]: r for r in services.students_roster()}
        for student in (self.s1, self.s2):
            detail = services.student_detail(student)
            self.assertEqual(detail["accuracy"], roster[student.id]["accuracy"])
            self.assertEqual(detail["exercises_done"], roster[student.id]["exercises_done"])

    def test_class_accuracy_is_correct_over_total(self):
        metrics = services.class_metrics()
        self.assertEqual(metrics["total_students"], 2)
        self.assertEqual(metrics["avg_accuracy"], 50)

    def test_s1_solved_two_exercises(self):
        self.assertEqual(services.student_detail(self.s1)["exercises_done"], 2)

    def test_misconception_breakdown_returns_mock(self):
        # analytics are mocked pending rework; panel still renders non-empty
        self.assertTrue(services.misconception_breakdown())


class TopicCompletionTests(TestCase):
    def setUp(self):
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.s1 = Profile.objects.create(email="s1@x.com", name="S One", role=Profile.ROLE_STUDENT)
        self.topic = Topic.objects.create(title="T1", created_by=self.teacher)
        self.published = Exercise.objects.create(
            topic=self.topic, title="Pub", description="d", difficulty="beginner",
            hint="h", target_formula="G p", is_published=True,
        )
        self.draft = Exercise.objects.create(
            topic=self.topic, title="Draft", description="d", difficulty="beginner",
            hint="h", target_formula="G p", is_published=False,
        )
        # the one enrolled student solves the published exercise -> 100%
        Attempt.objects.create(exercise=self.published, student=self.s1, is_correct=True, formula_input="G p")

    def test_draft_excluded_from_module_completion(self):
        (module,) = [m for m in services.topic_completion() if m["name"] == "T1"]
        # draft's guaranteed 0% must not drag the average down from 100
        self.assertEqual(module["completion"], 100)


class MultiPartAnalyticsTests(TestCase):
    def setUp(self):
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.s1 = Profile.objects.create(email="s1@x.com", name="S One", role=Profile.ROLE_STUDENT)
        self.s2 = Profile.objects.create(email="s2@x.com", name="S Two", role=Profile.ROLE_STUDENT)
        self.topic = Topic.objects.create(title="T1", created_by=self.teacher)

        self.e1 = Exercise.objects.create(
            topic=self.topic, title="E1", description="d", difficulty="beginner",
            hint="h", target_formula="G p", is_published=True,
        )
        self.e2 = Exercise.objects.create(
            topic=self.topic, title="E2", description="d", difficulty="beginner",
            hint="", is_published=True, exercise_type="english_to_formula",
            declared_aps=["p"],
        )
        self.p1 = ExercisePart.objects.create(
            exercise=self.e2, position=0, prompt="always p", formula="G p",
        )
        self.p2 = ExercisePart.objects.create(
            exercise=self.e2, position=1, prompt="eventually p", formula="F p",
        )
        self.e3 = Exercise.objects.create(
            topic=self.topic, title="E3", description="d", difficulty="beginner",
            hint="", is_published=True, exercise_type="judge",
        )
        self.pj = ExercisePart.objects.create(exercise=self.e3, formula="G a")
        self.e4 = Exercise.objects.create(
            topic=self.topic, title="E4", description="d", difficulty="beginner",
            hint="", is_published=True, exercise_type="path_exhibit",
        )
        self.pp = ExercisePart.objects.create(exercise=self.e4, formula="F p")

        # s1: 5 attempts, 4 correct — solves E1, E2 (both parts), E4
        Attempt.objects.create(exercise=self.e1, student=self.s1, is_correct=False, formula_input="F p")
        Attempt.objects.create(exercise=self.e1, student=self.s1, is_correct=True, formula_input="G p")
        Attempt.objects.create(
            exercise=self.e2, student=self.s1, part=self.p1, is_correct=True, formula_input="G p",
        )
        Attempt.objects.create(
            exercise=self.e2, student=self.s1, part=self.p2, is_correct=True, formula_input="F p",
        )
        Attempt.objects.create(
            exercise=self.e4, student=self.s1, part=self.pp, is_correct=True,
            formula_input=None, answer={"prefix": ["s0"], "cycle": ["s1", "s0"]},
            misconception="",
        )
        # s2: 3 attempts, 1 correct — solves nothing (E2 only partially)
        Attempt.objects.create(exercise=self.e1, student=self.s2, is_correct=False, formula_input="F p")
        Attempt.objects.create(
            exercise=self.e2, student=self.s2, part=self.p1, is_correct=True, formula_input="G p",
        )
        Attempt.objects.create(
            exercise=self.e3, student=self.s2, part=self.pj, is_correct=False,
            formula_input=None, answer={"verdict": "holds"}, misconception="",
        )

    def test_roster_exercises_done_uses_all_parts_rule(self):
        roster = {r["id"]: r for r in services.students_roster()}
        self.assertEqual(roster[self.s1.id]["exercises_done"], 3)
        self.assertEqual(roster[self.s2.id]["exercises_done"], 0)

    def test_roster_accuracy_is_attempt_level(self):
        roster = {r["id"]: r for r in services.students_roster()}
        self.assertEqual(roster[self.s1.id]["accuracy"], 80)
        self.assertEqual(roster[self.s2.id]["accuracy"], 33)

    def test_exercise_rows_multipart_raw_attempts_and_completion(self):
        rows = {r["id"]: r for r in services.exercise_rows()}
        self.assertEqual(rows[self.e2.id]["attempts"], 3)
        self.assertEqual(rows[self.e2.id]["completion"], 50)

    def test_exercise_rows_partless_completion_unchanged(self):
        rows = {r["id"]: r for r in services.exercise_rows()}
        self.assertEqual(rows[self.e1.id]["completion"], 50)

    def test_student_detail_partial_multipart_not_done(self):
        detail = services.student_detail(self.s2)
        self.assertEqual(detail["exercises_done"], 0)
        e2_entries = [h for h in detail["history"] if h["exercise"].startswith("E2")]
        self.assertEqual(len(e2_entries), 1)
        self.assertIn("Part 1", e2_entries[0]["exercise"])

    def test_history_renders_judge_and_path_answers(self):
        s2_history = services.student_detail(self.s2)["history"]
        (judge_entry,) = [h for h in s2_history if h["exercise"].startswith("E3")]
        self.assertTrue(judge_entry["formula"].startswith("judged:"))
        self.assertFalse(judge_entry["result"])

        s1_history = services.student_detail(self.s1)["history"]
        (path_entry,) = [h for h in s1_history if h["exercise"].startswith("E4")]
        self.assertTrue(path_entry["formula"].startswith("path:"))
        self.assertTrue(path_entry["result"])

    def test_student_detail_all_parts_done_counts(self):
        self.assertEqual(services.student_detail(self.s1)["exercises_done"], 3)

    def test_class_metrics_accuracy_stays_attempt_level(self):
        # 5 correct of 8 attempts = 62.5 -> half-up 63
        self.assertEqual(services.class_metrics()["avg_accuracy"], 63)
