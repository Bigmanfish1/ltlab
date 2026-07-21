import uuid

from django.db import models
from django.db.models.functions import Lower

from apps.accounts.models import Profile

DIFFICULTY_CHOICES = [
    ("beginner", "Beginner"),
    ("intermediate", "Intermediate"),
    ("advanced", "Advanced"),
]

EXERCISE_TYPE_CHOICES = [
    ("model_check", "Write a formula that holds"),
    ("judge", "Judge formulas + counterexample"),
    ("path_exhibit", "Exhibit a satisfying path"),
    ("english_to_formula", "English requirement to formula"),
    ("build_kripke", "Build a Kripke structure that satisfies a formula"),
    ("buchi_construct", "Draw a Büchi automaton for a formula"),
    ("buchi_word", "Give a word a Büchi automaton accepts"),
]


class Topic(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="topics"
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
    target_formula = models.TextField(null=True, blank=True)
    image_url = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    kripke_structure = models.JSONField(null=True, blank=True)
    allowed_operators = models.JSONField(default=list, blank=True)
    hints = models.JSONField(default=list, blank=True)
    is_published = models.BooleanField(default=False)
    # set on first publish, never reset — gates exercise-type changes
    ever_published = models.BooleanField(default=False)
    position = models.IntegerField(default=0)
    exercise_type = models.CharField(
        max_length=20, choices=EXERCISE_TYPE_CHOICES, default="model_check"
    )
    declared_aps = models.JSONField(default=list, blank=True)
    # buchi_construct only: also ask whether the drawn automaton is
    # deterministic (MCL5 p.19), graded against the student's own drawing
    ask_determinism = models.BooleanField(default=False)

    class Meta:
        db_table = "Exercises"
        ordering = ["position", "id"]

    def __str__(self):
        return self.title


class ExercisePart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="parts"
    )
    position = models.IntegerField(default=0)
    # prompt: the English requirement (english_to_formula); unused for judge/path_exhibit
    # formula: the displayed formula (judge/path_exhibit); the hidden target (english_to_formula)
    prompt = models.TextField(blank=True, default="")
    formula = models.TextField()
    hints = models.JSONField(default=list, blank=True)
    # judge only: whether formula holds on the graph, computed at save so the
    # per-submission grade need not re-run the model checker (null = uncomputed)
    answer_holds = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "ExerciseParts"
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.exercise_id} · part {self.position}"


class Attempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="attempts")
    part = models.ForeignKey(
        ExercisePart,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attempts",
    )
    formula_input = models.TextField(null=True, blank=True)
    # judge: {"verdict": "holds"} or {"verdict": "violated", "prefix": [...], "cycle": [...]}
    # path_exhibit: {"prefix": [...], "cycle": [...]}; formula types: NULL
    answer = models.JSONField(null=True, blank=True)
    is_correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    hints_used = models.IntegerField(default=0)
    # NULL = not yet classified; "" = classified, no misconception; else a bucket key
    misconception = models.CharField(max_length=32, null=True, blank=True)

    class Meta:
        db_table = "Attempts"

    def __str__(self):
        return f"{self.student_id} · {self.exercise_id} · {'ok' if self.is_correct else 'x'}"
