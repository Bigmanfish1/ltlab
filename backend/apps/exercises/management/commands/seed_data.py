"""Minimal local seed: one teacher, a few students, two modules, a handful of
attempts — enough for both the teacher pages and the student pages to render.

Kept deliberately small (ASCII LTL formulas, get_or_create everywhere) so it is
idempotent and low-risk. Run:  python manage.py seed_data
"""

from django.core.management.base import BaseCommand

from apps.accounts.models import Profile
from apps.exercises.models import Attempt, Exercise, Topic

SEED_DOMAIN = "seed.ltlab"

STUDENTS = ["Amara Dlamini", "Sipho Ndlovu", "Jamie Kim"]

MODULES = [
    {
        "title": "Kripke Structures",
        "description": "Modelling systems as state-transition graphs.",
        "exercises": [
            {
                "title": "Always eventually green",
                "description": "The light is always eventually green.",
                "difficulty": "beginner",
                "target_formula": "G F green",
                "hint": "Combine G (globally) with F (eventually).",
            },
            {
                "title": "Never red and green",
                "description": "Red and green are never true together.",
                "difficulty": "beginner",
                "target_formula": "G !(red & green)",
                "hint": "Use G with a negated conjunction.",
            },
        ],
    },
    {
        "title": "LTL Operators",
        "description": "Globally, Finally, Next and Until.",
        "exercises": [
            {
                "title": "Yellow leads to red",
                "description": "Whenever yellow holds, red eventually follows.",
                "difficulty": "intermediate",
                "target_formula": "G (yellow -> F red)",
                "hint": "G over an implication whose right side is F red.",
            },
            {
                "title": "Next is green",
                "description": "Green holds in the next state.",
                "difficulty": "intermediate",
                "target_formula": "X green",
                "hint": "X refers only to the immediately following state.",
            },
        ],
    },
]

# (exercise title, submitted formula, is_correct) per student.
# Wrong formulas are deliberate misconception variants so the teacher
# breakdown has something to classify.
ATTEMPTS = [
    # Amara — strong, solves all
    (0, [("Always eventually green", "G F green", True),
         ("Never red and green", "G !(red & green)", True),
         ("Yellow leads to red", "G (yellow -> F red)", True),
         ("Next is green", "X green", True)]),
    # Sipho — partial, one misconception then a solve
    (1, [("Always eventually green", "F green", False),      # dropped G
         ("Always eventually green", "G F green", True),
         ("Next is green", "F green", False)]),               # X vs F
    # Jamie — early, one wrong
    (2, [("Yellow leads to red", "yellow -> F red", False)]), # missing global
]


class Command(BaseCommand):
    help = "Seed a minimal dataset for local development (teacher, students, modules, attempts)."

    def handle(self, *args, **options):
        teacher, _ = Profile.objects.get_or_create(
            email=f"teacher@{SEED_DOMAIN}",
            defaults={"name": "Dr Timm", "role": Profile.ROLE_TEACHER},
        )

        students = []
        for full_name in STUDENTS:
            handle = full_name.split()[0].lower()
            student, _ = Profile.objects.get_or_create(
                email=f"{handle}@{SEED_DOMAIN}",
                defaults={"name": full_name, "role": Profile.ROLE_STUDENT},
            )
            students.append(student)

        exercises = {}
        for position, mod in enumerate(MODULES):
            topic, _ = Topic.objects.get_or_create(
                title=mod["title"],
                defaults={
                    "description": mod["description"],
                    "created_by": teacher,
                    "position": position,
                },
            )
            for ex_pos, ex in enumerate(mod["exercises"]):
                exercise, _ = Exercise.objects.get_or_create(
                    title=ex["title"],
                    topic=topic,
                    defaults={
                        "description": ex["description"],
                        "difficulty": ex["difficulty"],
                        "target_formula": ex["target_formula"],
                        "hint": ex["hint"],
                        "is_published": True,
                        "position": ex_pos,
                    },
                )
                exercises[ex["title"]] = exercise

        created = 0
        for student_idx, rows in ATTEMPTS:
            student = students[student_idx]
            for title, formula, is_correct in rows:
                _, made = Attempt.objects.get_or_create(
                    exercise=exercises[title],
                    student=student,
                    formula_input=formula,
                    defaults={"is_correct": is_correct},
                )
                created += int(made)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Profile.objects.count()} profiles, {Exercise.objects.count()} exercises, "
            f"{created} new attempts."
        ))
