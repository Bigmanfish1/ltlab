import uuid
from django.db import models 
from apps.accounts.models import Profile

class Difficulty(models.TextChoices):
    BEGINNER = 'beginner', 'Beginner'
    INTERMEDIATE = 'intermediate', 'Intermediate'
    ADVANCED = 'advanced', 'Advanced'

class Topic(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="Topics",
    )

    class Meta:
        db_table = 'Topics'

    def __str__(self):
        return self.title

class Exercise(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    topic = models.ForeignKey(
        Topic,
        on_delete=models.CASCADE,
        related_name="Exercises",
    )
    title = models.CharField(max_length=255)
    description = models.TextField()
    difficulty = models.CharField(
        max_length=20,
        choices=Difficulty.choices,
        default=Difficulty.BEGINNER,
    )
    target_formula = models.TextField()
    hint = models.TextField()

    class Meta:
        db_table = "Exercises"

    def __str__(self):
        return self.title
    
class Attempt(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name='Attempts')
    student = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='Attempts')
    formula_input = models.TextField()
    is_correct = models.BooleanField()

    class Meta:
        db_table = 'Attempts'

    def __str__(self):
        return f"{self.student} - {self.exercise} ({self.created_at})"