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

    def test_misconception_breakdown_buckets_wrong_attempts(self):
        buckets = {m["key"] for m in services.misconception_breakdown()}
        self.assertIn("g_vs_f", buckets)
        self.assertIn("missing_global", buckets)

    def test_breakdown_backfills_and_is_idempotent(self):
        services.misconception_breakdown()
        # every wrong attempt now carries a stored bucket (NULL only means unclassified)
        self.assertFalse(
            Attempt.objects.filter(is_correct=False, misconception__isnull=True).exists()
        )
        # a second load classifies nothing new — same result, no pending rows
        first = services.misconception_breakdown()
        self.assertEqual(first, services.misconception_breakdown())


class BackfillTypeGuardTests(TestCase):
    def setUp(self):
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.student = Profile.objects.create(email="s@x.com", name="S", role=Profile.ROLE_STUDENT)
        self.topic = Topic.objects.create(title="T1", created_by=self.teacher)
        self.english = Exercise.objects.create(
            topic=self.topic, title="Eng", description="d", difficulty="beginner",
            hint="", is_published=True, exercise_type="english_to_formula",
            declared_aps=["p", "q"],
        )
        self.part = ExercisePart.objects.create(
            exercise=self.english, prompt="always eventually p", formula="G F p",
        )

    def test_english_attempt_classified_against_part_target(self):
        attempt = Attempt.objects.create(
            exercise=self.english, student=self.student, part=self.part,
            is_correct=False, formula_input="F G p",
        )
        services._backfill_misconceptions()
        attempt.refresh_from_db()
        self.assertEqual(attempt.misconception, "g_vs_f")

    def test_judge_attempt_excluded_from_backfill(self):
        judge = Exercise.objects.create(
            topic=self.topic, title="Judge", description="d", difficulty="beginner",
            hint="", is_published=True, exercise_type="judge",
        )
        judge_part = ExercisePart.objects.create(exercise=judge, formula="G a")
        attempt = Attempt.objects.create(
            exercise=judge, student=self.student, part=judge_part,
            is_correct=False, answer={"verdict": "holds"},
        )
        services._backfill_misconceptions()
        attempt.refresh_from_db()
        self.assertIsNone(attempt.misconception)

    def test_partless_english_attempt_excluded(self):
        # english grading always sets part; a partless row on an english
        # exercise has no per-part target and must not be classified
        attempt = Attempt.objects.create(
            exercise=self.english, student=self.student,
            is_correct=False, formula_input="F G p",
        )
        services._backfill_misconceptions()
        attempt.refresh_from_db()
        self.assertIsNone(attempt.misconception)


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
