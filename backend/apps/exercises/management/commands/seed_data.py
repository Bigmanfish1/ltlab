# apps/exercises/management/commands/seed_data.py

import random

from django.core.management.base import BaseCommand, CommandError

from apps.exercises.models import Topic, Exercise, Attempt, Difficulty
from apps.accounts.models import Profile


class Command(BaseCommand):
    help = "Seeds the database with sample topics, exercises, and attempts for local development"

    def handle(self, *args, **options):
        profile = Profile.objects.first()
        student = Profile.objects.filter(role=Profile.ROLE_STUDENT).first()
        if not student:
            raise CommandError(
                "No student Profile found. Sign up at least one student via "
                "Supabase Auth first, then re-run this command."
            )
        self.stdout.write(self.style.SUCCESS(f"Using student: {student.email}"))

        topics_data = [
            {
                "title": "Kripke Structures",
                "description": "Modeling systems as state-transition graphs.",
                "exercises": [
                    {
                        "title": "Identify states and transitions",
                        "description": "Given a description, draw the Kripke structure.",
                        "difficulty": Difficulty.BEGINNER,
                        "target_formula": "",
                        "hint": "A Kripke structure is a set of states, transitions, and labeling.",
                    },
                    {
                        "title": "Label states with atomic propositions",
                        "description": "Assign propositions to each state correctly.",
                        "difficulty": Difficulty.BEGINNER,
                        "target_formula": "",
                        "hint": "Each state gets the set of propositions true in it.",
                    },
                ],
            },
            {
                "title": "LTL Operators",
                "description": "Globally, Finally, Next, and Until operators.",
                "exercises": [
                    {
                        "title": "Mutual exclusion: red and green",
                        "description": "Write a formula stating red and green can never both be true.",
                        "difficulty": Difficulty.BEGINNER,
                        "target_formula": "G ¬(red ∧ green)",
                        "hint": "Use G (Globally) with negation.",
                    },
                    {
                        "title": "Eventually green after red",
                        "description": "Write a formula: whenever red holds, green eventually follows.",
                        "difficulty": Difficulty.INTERMEDIATE,
                        "target_formula": "G (red → F green)",
                        "hint": "Combine G with F (Finally/Eventually).",
                    },
                    {
                        "title": "Until operator practice",
                        "description": "Write a formula: red holds until green becomes true.",
                        "difficulty": Difficulty.INTERMEDIATE,
                        "target_formula": "red U green",
                        "hint": "U (Until) takes two operands: left U right.",
                    },
                    {
                        "title": "Next operator practice",
                        "description": "Write a formula stating that green holds in the very next state.",
                        "difficulty": Difficulty.INTERMEDIATE,
                        "target_formula": "X green",
                        "hint": "X (Next) refers only to the immediately following state.",
                    },
                ],
            },
            {
                "title": "Fairness & Liveness",
                "description": "Ensuring progress: something good eventually happens infinitely often.",
                "exercises": [
                    {
                        "title": "Strong fairness formula",
                        "description": "Write a formula requiring a process runs infinitely often if enabled infinitely often.",
                        "difficulty": Difficulty.ADVANCED,
                        "target_formula": "G F enabled → G F runs",
                        "hint": "Combine G F (infinitely often) on both sides of an implication.",
                    },
                    {
                        "title": "No starvation",
                        "description": "Write a liveness formula ensuring a waiting process is eventually scheduled.",
                        "difficulty": Difficulty.ADVANCED,
                        "target_formula": "G (waiting → F scheduled)",
                        "hint": "G + F, similar structure to earlier 'eventually' exercises.",
                    },
                ],
            },
        ]

        for topic_data in topics_data:
            topic, created = Topic.objects.get_or_create(
                title=topic_data["title"],
                defaults={"description": topic_data["description"], "created_by": profile},
            )
            self.stdout.write(self.style.SUCCESS(
                f"Topic: {topic.title} ({'created' if created else 'exists'})"
            ))

            for ex_data in topic_data["exercises"]:
                exercise, created = Exercise.objects.get_or_create(
                    title=ex_data["title"],
                    topic=topic,
                    defaults={
                        "description": ex_data["description"],
                        "difficulty": ex_data["difficulty"],
                        "target_formula": ex_data["target_formula"],
                        "hint": ex_data["hint"],
                    },
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  Exercise: {exercise.title} ({'created' if created else 'exists'})"
                ))

        # --- Seed attempts: vary completion per topic for a realistic dashboard
        completion_plan = {
            "Kripke Structures": 1.0,     # fully complete
            "LTL Operators": 0.5,         # halfway complete
            "Fairness & Liveness": 0.0,   # untouched
        }

        for topic_title, fraction_correct in completion_plan.items():
            topic = Topic.objects.get(title=topic_title)
            exercises = list(topic.Exercises.all())  # swap to topic.Exercises if not renamed
            random.shuffle(exercises)

            if fraction_correct == 0.0:
                self.stdout.write(self.style.SUCCESS(f"Skipping attempts for {topic_title} (untouched)"))
                continue

            num_correct = round(len(exercises) * fraction_correct)

            for i, exercise in enumerate(exercises):
                should_be_correct = i < num_correct

                if should_be_correct and random.random() < 0.4:
                    Attempt.objects.get_or_create(
                        exercise=exercise,
                        student=student,
                        formula_input="G ¬(wrong ∧ attempt)",
                        defaults={"is_correct": False},
                    )

                Attempt.objects.get_or_create(
                    exercise=exercise,
                    student=student,
                    formula_input=exercise.target_formula or "sample_input",
                    defaults={"is_correct": should_be_correct},
                )

            self.stdout.write(self.style.SUCCESS(
                f"Attempts seeded for {topic_title}: {num_correct}/{len(exercises)} correct"
            ))

        self.stdout.write(self.style.SUCCESS("Seeding complete."))
