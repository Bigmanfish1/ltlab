import uuid

from django.db import models
from django.db.models.functions import Lower

from apps.accounts.models import Profile

DIFFICULTY_CHOICES = [
    ("beginner", "Beginner"),
    ("intermediate", "Intermediate"),
    ("advanced", "Advanced"),
]


class Topic(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        Profile, on_delete=models.CASCADE, db_column="created_by", related_name="topics"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    visible = models.BooleanField(default=True)
    position = models.IntegerField(default=0)
    unlocks_after = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="unlocks"
    )

    class Meta:
        db_table = "Topics"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(Lower("title"), name="uniq_topic_title_ci"),
        ]

    def __str__(self):
        return self.title


class Exercise(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="exercises")
    title = models.TextField()
    description = models.TextField()
    difficulty = models.CharField(max_length=12, choices=DIFFICULTY_CHOICES)
    hint = models.TextField()
    target_formula = models.TextField()
    image_url = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    kripke_structure = models.JSONField(null=True, blank=True)
    allowed_operators = models.JSONField(default=list, blank=True)
    hints = models.JSONField(default=list, blank=True)
    is_published = models.BooleanField(default=False)
    position = models.IntegerField(default=0)

    class Meta:
        db_table = "Exercises"
        ordering = ["position", "id"]

    def __str__(self):
        return self.title


class Attempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="attempts")
    formula_input = models.TextField(null=True, blank=True)
    is_correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    hints_used = models.IntegerField(default=0)
    # NULL = not yet classified; "" = classified, no misconception; else a bucket key
    misconception = models.CharField(max_length=32, null=True, blank=True)

    class Meta:
        db_table = "Attempts"

    def __str__(self):
        return f"{self.student_id} · {self.exercise_id} · {'ok' if self.is_correct else 'x'}"
