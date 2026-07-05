from django.test import TestCase

from apps.accounts.models import Profile
from apps.exercises import services
from apps.exercises.models import Attempt, Exercise, Topic


class ClassifyMisconceptionTests(TestCase):
    def test_identical_is_none(self):
        self.assertIsNone(services.classify_misconception("G F p", "G F p"))

    def test_gf_vs_fg(self):
        self.assertEqual(services.classify_misconception("G F green", "F G green"), "gf_vs_fg")

    def test_missing_global(self):
        self.assertEqual(services.classify_misconception("G F green", "F green"), "missing_global")

    def test_f_vs_x(self):
        self.assertEqual(
            services.classify_misconception("G (req -> F grant)", "G (req -> X grant)"), "f_vs_x"
        )

    def test_safety_vs_liveness(self):
        self.assertEqual(services.classify_misconception("F grant", "G grant"), "safety_vs_liveness")

    def test_fallthrough_english(self):
        self.assertEqual(services.classify_misconception("G (a & b)", "G (a & c)"), "english_to_ltl")


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
        self.assertIn("gf_vs_fg", buckets)
        self.assertIn("missing_global", buckets)
