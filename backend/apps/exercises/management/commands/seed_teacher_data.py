import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import Profile
from apps.exercises.models import Attempt, Exercise, Topic
from apps.exercises.services import classify_misconception

SEED_DOMAIN = "seed.ltlab"

FIRST_NAMES = [
    "Amara", "Sipho", "Jamie", "Riley", "Thabo", "Lerato", "Nadia", "Kabelo",
    "Chen", "Priya", "Omar", "Zanele", "Liam", "Fatima", "Noah", "Aisha",
    "Diego", "Mei", "Kwame", "Sofia", "Ravi", "Naledi", "Ethan", "Yara",
    "Tariq", "Grace", "Bongani", "Hana", "Marco", "Lindiwe", "Ivan", "Zara",
    "Musa", "Elena", "Sanele", "Kim", "Ayanda", "Leo", "Nomsa", "Ken",
]
LAST_NAMES = [
    "Dlamini", "Ndlovu", "Kim", "Wong", "Pillay", "Mokoena", "Khan", "Sithole",
    "Patel", "Nkosi", "Silva", "Zhang", "Mbeki", "Osei", "Rossi", "Naidoo",
    "Botha", "Adebayo", "Cohen", "Mahlangu",
]

GRAPHS = {
    "reqgrant": {
        "nodes": [
            ("idle", ["idle"], True, 150, 270),
            ("req", ["req"], False, 340, 130),
            ("grant", ["grant"], False, 530, 270),
        ],
        "edges": [("idle", "req"), ("req", "grant"), ("grant", "idle")],
    },
    "traffic": {
        "nodes": [
            ("red", ["red"], True, 150, 250),
            ("green", ["green"], False, 340, 130),
            ("amber", ["amber"], False, 530, 250),
        ],
        "edges": [("red", "green"), ("green", "amber"), ("amber", "red")],
    },
    "mutex": {
        "nodes": [
            ("n", [], True, 150, 250),
            ("c1", ["c1"], False, 350, 150),
            ("c2", ["c2"], False, 350, 350),
        ],
        "edges": [("n", "c1"), ("n", "c2"), ("c1", "n"), ("c2", "n")],
    },
    "pulse": {
        "nodes": [
            ("s0", ["p"], True, 180, 220),
            ("s1", [], False, 420, 220),
        ],
        "edges": [("s0", "s1"), ("s1", "s0")],
    },
}

TEMPLATES = [
    ("reqgrant", "G (req → F grant)", ["G", "F", "→"]),
    ("traffic", "G F green", ["G", "F"]),
    ("mutex", "G ¬(c1 ∧ c2)", ["G", "¬", "∧"]),
    ("pulse", "G F p", ["G", "F"]),
]

TOPICS = [
    ("Kripke Basics", 6, "beginner"),
    ("Kripke Structures", 8, "beginner"),
    ("LTL Operators", 10, "intermediate"),
    ("CTL Semantics", 7, "advanced"),
    ("Fairness & Liveness", 5, "advanced"),
]

EXERCISE_NAMES = [
    "Basic Kripke Structure", "Atomic Propositions", "Labelling States",
    "Transition Relations", "Initial States", "Reachability", "Deadlock States",
    "Self Loops", "Deterministic Transitions", "Nondeterminism",
    "Next-State Reasoning", "Path Enumeration", "State Merging", "Bisimulation",
    "Always", "Eventually", "Always Eventually", "Next Operator", "Until Operator",
    "Weak Until", "Release Operator", "Request-Grant Protocol", "Operator Precedence",
    "Nested Temporal", "Mutual Exclusion", "Nested Modalities", "Path Quantifiers",
    "Existential Until", "Universal Next", "Fairness in CTL", "Branching Time",
    "Fairness Constraints", "Strong Fairness", "Liveness Properties",
    "Starvation Freedom", "Progress",
]

HINTS = [
    "Think about which temporal operator the requirement needs.",
    "Combine the always (G) and eventually (F) operators.",
    "Re-read the plain-English requirement carefully.",
]


def _graph(key):
    spec = GRAPHS[key]
    nodes = [
        {
            "data": {"id": nid, "name": nid, "label": nid, "props": props, "initial": init},
            "position": {"x": x, "y": y},
        }
        for nid, props, init, x, y in spec["nodes"]
    ]
    edges = [
        {"data": {"id": f"e{i}", "source": s, "target": t}}
        for i, (s, t) in enumerate(spec["edges"])
    ]
    return {"elements": {"nodes": nodes, "edges": edges}}


def _wrong_variants(target):
    compact = target.replace(" ", "")
    out = []
    if "GF" in compact:
        out.append(target.replace("G F", "F G"))
    if "FG" in compact:
        out.append(target.replace("F G", "G F"))
    if target.startswith("G "):
        out.append(target[2:])          # drop outer G -> missing_global
    if target.startswith("F "):
        out.append(target[2:])          # drop outer F -> missing_eventually
    if "F" in target:
        out.append(target.replace("F", "X", 1))
    out.append("!(" + target + ")")     # negation -> inverted
    if "(" in target:
        out.append(target.replace("(", "", 1))
    out.append("p U q")                 # unrelated -> mistranslation
    return [w for w in dict.fromkeys(out) if w and w != target]


class Command(BaseCommand):
    help = "Seed a coherent teacher dataset (local dev only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to run with DEBUG=False (protects production data).")

        rng = random.Random(42)
        now = timezone.now()

        # Scoped to seed-owned rows only (see SEED_DOMAIN) so locally-authored
        # non-seed content survives a re-run. Deleting the seed topics cascades
        # their exercises; deleting seed students cascades their attempts.
        Attempt.objects.filter(student__email__endswith=SEED_DOMAIN).delete()
        Topic.objects.filter(created_by__email__endswith=SEED_DOMAIN).delete()
        Profile.objects.filter(email__endswith=SEED_DOMAIN).delete()

        # Dedicated seed teacher so its topics are seed-owned (and thus scoped above).
        teacher = Profile.objects.create(
            email=f"teacher@{SEED_DOMAIN}", name="Dr Timm", role=Profile.ROLE_TEACHER
        )

        students = []
        used = set()
        for i in range(40):
            while True:
                fn = rng.choice(FIRST_NAMES)
                ln = rng.choice(LAST_NAMES)
                name = f"{fn} {ln}"
                if name not in used:
                    used.add(name)
                    break
            email = f"{fn}.{ln}.{i}@{SEED_DOMAIN}".lower()
            students.append(Profile.objects.create(email=email, name=name, role=Profile.ROLE_STUDENT))

        topics = []
        prev = None
        for pos, (title, _count, _diff) in enumerate(TOPICS):
            topic = Topic.objects.create(
                title=title,
                description=f"{title} — guided exercises.",
                created_by=teacher,
                position=pos,
                visible=pos < len(TOPICS) - 1,
                unlocks_after=prev,
            )
            topics.append(topic)
            prev = topic

        exercises = []
        name_iter = iter(EXERCISE_NAMES)
        for topic, (title, count, base_diff) in zip(topics, TOPICS):
            for j in range(count):
                key, target, ops = TEMPLATES[len(exercises) % len(TEMPLATES)]
                difficulty = base_diff if j < count - 2 else "advanced"
                ex = Exercise.objects.create(
                    topic=topic,
                    title=next(name_iter),
                    description=f"Write an LTL formula for the {topic.title.lower()} scenario.",
                    difficulty=difficulty,
                    hint=HINTS[0],
                    hints=HINTS,
                    target_formula=target,
                    allowed_operators=ops,
                    kripke_structure=_graph(key),
                    is_published=True,
                    position=j,
                )
                exercises.append(ex)

        exercises[-1].is_published = False
        exercises[-1].save(update_fields=["is_published"])

        rotation = ["missing_global", "f_vs_x", "g_vs_f", "missing_eventually", "inverted", "mistranslation"]
        ex_wrong = {}
        ex_bucket = {}
        for idx, ex in enumerate(exercises):
            variants = _wrong_variants(ex.target_formula)
            want = rotation[idx % len(rotation)]
            match = next((v for v in variants if classify_misconception(ex.target_formula, v) == want), None)
            ex_wrong[ex.id] = match or variants[0]
            ex_bucket[ex.id] = classify_misconception(ex.target_formula, ex_wrong[ex.id]) or ""

        stamps = []
        for si, student in enumerate(students):
            mastery = rng.uniform(0.35, 0.98)
            reach = max(1, int(mastery * len(exercises)))
            active_recent = si < 26
            for ex in exercises[:reach]:
                wrong = ex_wrong[ex.id]
                solves = rng.random() < (0.6 + 0.35 * mastery)
                tries = rng.randint(0, 2) if solves else rng.randint(1, 3)
                day_span = rng.randint(0, 6) if active_recent else rng.randint(8, 40)
                base = now - timedelta(days=day_span, hours=rng.randint(0, 20))
                for k in range(tries):
                    stamps.append(_mk(ex, student, False, wrong, ex_bucket[ex.id],
                                      rng.randint(0, 2), base + timedelta(minutes=k * 7)))
                if solves:
                    stamps.append(_mk(ex, student, True, ex.target_formula, "",
                                      rng.randint(0, 1), base + timedelta(minutes=tries * 7)))

        created = Attempt.objects.bulk_create([a for a, _ in stamps])
        for attempt, (_obj, dt) in zip(created, stamps):
            Attempt.objects.filter(pk=attempt.pk).update(created_at=dt)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(students)} students, {len(topics)} topics, "
            f"{len(exercises)} exercises, {len(created)} attempts."
        ))


def _mk(exercise, student, correct, formula, misconception, hints_used, dt):
    attempt = Attempt(
        exercise=exercise,
        student=student,
        is_correct=correct,
        formula_input=formula,
        hints_used=hints_used,
        misconception=misconception,
    )
    return attempt, dt
