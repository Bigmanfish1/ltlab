"""Minimal local seed: one teacher, a few students, two modules, a handful of
attempts — enough for both the teacher pages and the student pages to render.

Kept deliberately small (ASCII LTL formulas, get_or_create everywhere) so it is
idempotent and low-risk. Run:  python manage.py seed_data
"""

from django.core.management.base import BaseCommand

from apps.accounts.models import Profile
from apps.exercises.constants import BUILDER_OPERATORS
from apps.exercises.models import Attempt, Exercise, ExercisePart, Topic

SEED_DOMAIN = "seed.ltlab"

STUDENTS = ["Amara Dlamini", "Sipho Ndlovu", "Jamie Kim"]

# Grading model-checks the student's formula against this graph, so every seed
# exercise needs one. This is the canonical request–grant model from the
# research proposal (Bentley & Timms, Fig. 1): s0{idle} (initial), s1{req},
# s2{req,grant}, with a TOTAL transition relation s0→s0, s0→s1, s1→s2, s2→s0.
REQUEST_GRANT = {
    "elements": {
        # positions matter: the editor lays out with "preset", so nodes without
        # coordinates would all stack at (0,0) and render as a single state
        "nodes": [
            {"data": {"id": "s0", "name": "s0", "props": ["idle"], "initial": True},
             "position": {"x": 220, "y": 220}},
            {"data": {"id": "s1", "name": "s1", "props": ["req"]},
             "position": {"x": 460, "y": 220}},
            {"data": {"id": "s2", "name": "s2", "props": ["req", "grant"]},
             "position": {"x": 340, "y": 400}},
        ],
        "edges": [
            {"data": {"id": "e_s0_s0", "source": "s0", "target": "s0"}},
            {"data": {"id": "e_s0_s1", "source": "s0", "target": "s1"}},
            {"data": {"id": "e_s1_s2", "source": "s1", "target": "s2"}},
            {"data": {"id": "e_s2_s0", "source": "s2", "target": "s0"}},
        ],
    },
}

# Every target_formula below HOLDS on REQUEST_GRANT, so a student who writes the
# described property is graded correct.
MODULES = [
    {
        "title": "Kripke Structures",
        "description": "A request–grant protocol modelled as a state-transition graph.",
        "exercises": [
            {
                "title": "Requests are eventually granted",
                "description": "Whenever a request is made, a grant eventually follows.",
                "difficulty": "beginner",
                "target_formula": "G (req -> F grant)",
                "hint": "Combine G (globally) with an implication whose right side is F grant.",
            },
            {
                "title": "Grant implies request",
                "description": "A grant never occurs without a request holding in the same state.",
                "difficulty": "beginner",
                "target_formula": "G (grant -> req)",
                "hint": "Use G over an implication from grant to req.",
            },
        ],
    },
    {
        "title": "LTL Operators",
        "description": "Globally, Finally, Next and Until on the request–grant model.",
        "exercises": [
            {
                "title": "Idle recurs forever",
                "description": "The system returns to the idle state infinitely often.",
                "difficulty": "intermediate",
                "target_formula": "G F idle",
                "hint": "Combine G (globally) with F (eventually).",
            },
            {
                "title": "Grant is followed by idle",
                "description": "Whenever grant holds, the next state is idle.",
                "difficulty": "intermediate",
                "target_formula": "G (grant -> X idle)",
                "hint": "X refers only to the immediately following state.",
            },
        ],
    },
]

# The coffee-machine specification exercise from the module slides (MCL3
# p.17–20), graded by language equivalence against each hidden target.
ENGLISH_MODULE = {
    "title": "Specification of Properties",
    "description": "Translate plain-English requirements into LTL formulas.",
}
ENGLISH_EXERCISE = {
    "title": "Coffee machine requirements",
    "description": (
        "A coffee machine lets users choose tea or coffee, accepts money, and "
        "delivers drinks. Translate each requirement into an LTL formula over "
        "the listed atomic propositions."
    ),
    "difficulty": "intermediate",
    "declared_aps": [
        "coffee_chosen", "tea_chosen", "money_inserted",
        "coffee_delivered", "tea_delivered",
    ],
    "parts": [
        ("once in a while someone chooses tea or coffee",
         "G F (tea_chosen | coffee_chosen)"),
        ("if coffee is chosen and next money is inserted coffee will be delivered",
         "G ((coffee_chosen & X money_inserted) -> F coffee_delivered)"),
        ("when coffee is chosen tea will not be delivered until tea is chosen",
         "G (coffee_chosen -> (!tea_delivered U tea_chosen))"),
    ],
}

# (student index, part position, submitted formula, is_correct) — the wrong
# ones are real slips (missing G, F-for-X) so Results shows genuine buckets.
ENGLISH_ATTEMPTS = [
    (0, 0, "G F (tea_chosen | coffee_chosen)", True),
    (1, 0, "F (tea_chosen | coffee_chosen)", False),
    (1, 0, "G F (tea_chosen | coffee_chosen)", True),
    (2, 1, "G ((coffee_chosen & F money_inserted) -> F coffee_delivered)", False),
]

# Path-exhibit exercise (MCL3 p.11 form: "a path π with π ⊨ φ?"). Every part
# formula verified satisfiable on REQUEST_GRANT via run_ltl_check("!(φ)").
PATH_EXERCISE = {
    "title": "Exhibit a satisfying path",
    "description": (
        "For each formula, trace an infinite path of the request–grant model "
        "that satisfies it."
    ),
    "difficulty": "intermediate",
    "parts": ["F grant", "G F idle", "X req"],
}

# (student index, part position, prefix, cycle, is_correct) — verified against
# run_trace_check: the wrong one is a real path on which the formula fails.
PATH_ATTEMPTS = [
    (0, 0, ["s0", "s1"], ["s2", "s0", "s1"], True),
    (1, 0, [], ["s0"], False),
]

# Judge exercise (MCL3 p.29 verbatim formulas). The slide's own graph is not
# in the RAG, so this graph over {a,b,c} was authored and its verdicts pinned
# empirically via run_ltl_check: F G c, G F c, (X !c) -> (X X c), G a violated;
# a U (G (b | c)) and (X X b) U (b | c) satisfied.
JUDGE_GRAPH = {
    "elements": {
        "nodes": [
            {"data": {"id": "s0", "name": "s0", "props": ["a"], "initial": True},
             "position": {"x": 220, "y": 220}},
            {"data": {"id": "s1", "name": "s1", "props": ["a", "b"]},
             "position": {"x": 460, "y": 220}},
            {"data": {"id": "s2", "name": "s2", "props": ["b", "c"]},
             "position": {"x": 340, "y": 400}},
        ],
        "edges": [
            {"data": {"id": "e_s0_s1", "source": "s0", "target": "s1"}},
            {"data": {"id": "e_s1_s1", "source": "s1", "target": "s1"}},
            {"data": {"id": "e_s1_s2", "source": "s1", "target": "s2"}},
            {"data": {"id": "e_s2_s2", "source": "s2", "target": "s2"}},
        ],
    },
}

JUDGE_EXERCISE = {
    "title": "Which formulae hold universally?",
    "description": (
        "Which of the following LTL formulae hold universally for the given "
        "Kripke structure? For each formula that does not hold, provide a "
        "counterexample."
    ),
    "difficulty": "advanced",
    "parts": [
        "F G c", "G F c", "(X !c) -> (X X c)",
        "G a", "a U (G (b | c))", "(X X b) U (b | c)",
    ],
}

# (exercise title, submitted formula, is_correct) per student. Wrong formulas
# genuinely fail on REQUEST_GRANT so the roster/accuracy stats are truthful.
ATTEMPTS = [
    # Amara — strong, solves all
    (0, [("Requests are eventually granted", "G (req -> F grant)", True),
         ("Grant implies request", "G (grant -> req)", True),
         ("Idle recurs forever", "G F idle", True),
         ("Grant is followed by idle", "G (grant -> X idle)", True)]),
    # Sipho — partial: one failing attempt then a solve
    (1, [("Requests are eventually granted", "F grant", False),   # F grant fails on (s0)ω
         ("Requests are eventually granted", "G (req -> F grant)", True),
         ("Idle recurs forever", "G idle", False)]),              # idle is not always true
    # Jamie — early, one wrong
    (2, [("Grant is followed by idle", "X idle", False)]),  # s0's successors aren't both idle
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
                        "kripke_structure": REQUEST_GRANT,
                        "allowed_operators": list(BUILDER_OPERATORS),
                    },
                )
                # keep the seed graph + operator set authoritative so re-running
                # fixes drift (and backfills rows created before these existed)
                if exercise.kripke_structure != REQUEST_GRANT or not exercise.allowed_operators:
                    exercise.kripke_structure = REQUEST_GRANT
                    exercise.allowed_operators = list(BUILDER_OPERATORS)
                    exercise.save(update_fields=["kripke_structure", "allowed_operators"])
                exercises[ex["title"]] = exercise

        english_topic, _ = Topic.objects.get_or_create(
            title=ENGLISH_MODULE["title"],
            defaults={
                "description": ENGLISH_MODULE["description"],
                "created_by": teacher,
                "position": len(MODULES),
            },
        )
        english, _ = Exercise.objects.get_or_create(
            title=ENGLISH_EXERCISE["title"],
            topic=english_topic,
            defaults={
                "description": ENGLISH_EXERCISE["description"],
                "difficulty": ENGLISH_EXERCISE["difficulty"],
                "hint": "",
                "is_published": True,
                "exercise_type": "english_to_formula",
                "declared_aps": ENGLISH_EXERCISE["declared_aps"],
                "allowed_operators": list(BUILDER_OPERATORS),
            },
        )
        english_parts = []
        for position, (prompt, formula) in enumerate(ENGLISH_EXERCISE["parts"]):
            part, _ = ExercisePart.objects.get_or_create(
                exercise=english,
                position=position,
                defaults={"prompt": prompt, "formula": formula},
            )
            english_parts.append(part)

        ltl_topic = Topic.objects.get(title="LTL Operators")
        path_ex, _ = Exercise.objects.get_or_create(
            title=PATH_EXERCISE["title"],
            topic=ltl_topic,
            defaults={
                "description": PATH_EXERCISE["description"],
                "difficulty": PATH_EXERCISE["difficulty"],
                "hint": "",
                "is_published": True,
                "position": 2,
                "exercise_type": "path_exhibit",
                "kripke_structure": REQUEST_GRANT,
                "allowed_operators": list(BUILDER_OPERATORS),
            },
        )
        path_parts = []
        for position, formula in enumerate(PATH_EXERCISE["parts"]):
            part, _ = ExercisePart.objects.get_or_create(
                exercise=path_ex,
                position=position,
                defaults={"formula": formula},
            )
            path_parts.append(part)

        judge_ex, _ = Exercise.objects.get_or_create(
            title=JUDGE_EXERCISE["title"],
            topic=ltl_topic,
            defaults={
                "description": JUDGE_EXERCISE["description"],
                "difficulty": JUDGE_EXERCISE["difficulty"],
                "hint": "",
                "is_published": True,
                "position": 3,
                "exercise_type": "judge",
                "kripke_structure": JUDGE_GRAPH,
                "allowed_operators": list(BUILDER_OPERATORS),
            },
        )
        for position, formula in enumerate(JUDGE_EXERCISE["parts"]):
            ExercisePart.objects.get_or_create(
                exercise=judge_ex,
                position=position,
                defaults={"formula": formula},
            )

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

        for student_idx, part_pos, formula, is_correct in ENGLISH_ATTEMPTS:
            _, made = Attempt.objects.get_or_create(
                exercise=english,
                student=students[student_idx],
                part=english_parts[part_pos],
                formula_input=formula,
                defaults={"is_correct": is_correct},
            )
            created += int(made)

        for student_idx, part_pos, prefix, cycle, is_correct in PATH_ATTEMPTS:
            _, made = Attempt.objects.get_or_create(
                exercise=path_ex,
                student=students[student_idx],
                part=path_parts[part_pos],
                answer={"prefix": prefix, "cycle": cycle},
                defaults={"is_correct": is_correct, "misconception": ""},
            )
            created += int(made)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Profile.objects.count()} profiles, {Exercise.objects.count()} exercises, "
            f"{created} new attempts."
        ))
