from django.test import TestCase

from apps.accounts.models import Profile
from apps.exercises.models import Attempt, Exercise, Topic


class ConformanceTests(TestCase):
    def test_db_table_names_match_supabase(self):
        self.assertEqual(Topic._meta.db_table, "Topics")
        self.assertEqual(Exercise._meta.db_table, "Exercises")
        self.assertEqual(Attempt._meta.db_table, "Attempts")
        self.assertEqual(Profile._meta.db_table, "Users")

    def test_created_by_column_conforms(self):
        column = Topic._meta.get_field("created_by").column
        self.assertEqual(column, "created_by")

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
