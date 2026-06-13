from django.db import models


class Profile(models.Model):
    ROLE_STUDENT = "student"
    ROLE_TEACHER = "teacher"
    ROLE_CHOICES = [
        (ROLE_STUDENT, "Student"),
        (ROLE_TEACHER, "Teacher"),
    ]

    supabase_user_id = models.UUIDField(unique=True)
    email = models.EmailField()
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_STUDENT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} ({self.role})"
