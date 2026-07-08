from django.core.management.base import BaseCommand

from apps.accounts.models import Profile
from apps.exercises.models import Topic, Exercise, Difficulty


class Command(BaseCommand):
    help = "Seeds the database with sample topics and exercises for local development"

    def handle(self, *args, **options):
        profile = Profile.objects.first()

        if profile is None:
            self.stdout.write(
                self.style.ERROR(
                    "No Profile found. Please sign in once or create a Profile before seeding."
                )
            )
            return

        topic, created = Topic.objects.get_or_create(
            title="LTL Basics",
            defaults={
                "description": "Introduction to Linear Temporal Logic operators",
                "created_by": profile,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Topic: {topic.title} ({'created' if created else 'exists'})"
            )
        )

        exercise1, created = Exercise.objects.get_or_create(
            title="Mutual exclusion: red and green",
            topic=topic,
            defaults={
                "description": "Write an LTL formula stating red and green can never both be true.",
                "difficulty": Difficulty.BEGINNER,
                "target_formula": "G !(red & green)",
                "hint": "Think about the Globally (G) operator combined with negation.",
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Exercise: {exercise1.title} ({'created' if created else 'exists'})"
            )
        )

        exercise2, created = Exercise.objects.get_or_create(
            title="Eventually green after red",
            topic=topic,
            defaults={
                "description": "Write a formula stating that whenever red holds, green eventually follows.",
                "difficulty": Difficulty.INTERMEDIATE,
                "target_formula": "G(red -> F green)",
                "hint": "Combine the Globally (G) and Finally (F) operators.",
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Exercise: {exercise2.title} ({'created' if created else 'exists'})"
            )
        )

        self.stdout.write(self.style.SUCCESS("Seeding complete."))