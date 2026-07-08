from django.db import models


class Profile(models.Model):
    ROLE_STUDENT = "student"
    ROLE_TEACHER = "teacher"
    ROLE_CHOICES = [
        (ROLE_STUDENT, "Student"),
        (ROLE_TEACHER, "Teacher"),
    ]

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_STUDENT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "Users"

    def __str__(self):
        return f"{self.email} ({self.role})"
