from django.test import TestCase

from apps.accounts.models import Profile
from apps.exercises.models import Attempt, Exercise, ExercisePart, Topic


class ConformanceTests(TestCase):
    def test_db_table_names_match_supabase(self):
        self.assertEqual(Topic._meta.db_table, "Topics")
        self.assertEqual(Exercise._meta.db_table, "Exercises")
        self.assertEqual(Attempt._meta.db_table, "Attempts")
        self.assertEqual(ExercisePart._meta.db_table, "ExerciseParts")
        self.assertEqual(Profile._meta.db_table, "Users")

    def test_created_by_column_conforms(self):
        # prod (rebuilt on UUID) uses Django's default FK column name
        column = Topic._meta.get_field("created_by").column
        self.assertEqual(column, "created_by_id")

    def test_cascade_delete_topic_removes_exercises_and_attempts(self):
        teacher = Profile.objects.create(email="t@x.com", role=Profile.ROLE_TEACHER)
        student = Profile.objects.create(email="s@x.com", role=Profile.ROLE_STUDENT)
        topic = Topic.objects.create(title="T", created_by=teacher)
        ex = Exercise.objects.create(
            topic=topic, title="E", description="d", difficulty="beginner",
            hint="h", target_formula="G p",
        )
        Attempt.objects.create(exercise=ex, student=student, is_correct=True, formula_input="G p")
        topic.delete()
        self.assertFalse(Exercise.objects.exists())
        self.assertFalse(Attempt.objects.exists())


class ExercisePartTests(TestCase):
    def setUp(self):
        teacher = Profile.objects.create(email="t@x.com", role=Profile.ROLE_TEACHER)
        self.student = Profile.objects.create(email="s@x.com", role=Profile.ROLE_STUDENT)
        topic = Topic.objects.create(title="T", created_by=teacher)
        self.exercise = Exercise.objects.create(
            topic=topic, title="E", description="d", difficulty="beginner", hint="",
        )

    def test_exercise_defaults(self):
        self.assertEqual(self.exercise.exercise_type, "model_check")
        self.assertEqual(self.exercise.declared_aps, [])
        self.assertEqual(self.exercise.parts.count(), 0)

    def test_parts_ordered_by_position(self):
        p2 = ExercisePart.objects.create(exercise=self.exercise, position=2, formula="G b")
        p1 = ExercisePart.objects.create(exercise=self.exercise, position=1, formula="F a")
        self.assertEqual(list(self.exercise.parts.all()), [p1, p2])

    def test_attempt_part_link_and_defaults(self):
        part = ExercisePart.objects.create(exercise=self.exercise, formula="F a")
        attempt = Attempt.objects.create(
            exercise=self.exercise, student=self.student, part=part,
            is_correct=False, answer={"verdict": "holds"},
        )
        self.assertIsNone(attempt.formula_input)
        self.assertEqual(part.attempts.get(), attempt)
        legacy = Attempt.objects.create(
            exercise=self.exercise, student=self.student, is_correct=True,
        )
        self.assertIsNone(legacy.part)
        self.assertIsNone(legacy.answer)

    def test_deleting_part_cascades_attempts_only_for_that_part(self):
        part = ExercisePart.objects.create(exercise=self.exercise, formula="F a")
        Attempt.objects.create(
            exercise=self.exercise, student=self.student, part=part, is_correct=False,
        )
        legacy = Attempt.objects.create(
            exercise=self.exercise, student=self.student, is_correct=True,
        )
        part.delete()
        self.assertEqual(list(Attempt.objects.all()), [legacy])
