"""ask_determinism flag on Exercises (teammate-owned table — 0005/0007's shape).

Opt-in per buchi_construct exercise: when set, the student also answers whether
the automaton they drew is deterministic (MCL5 p.19). Defaults false, so every
existing row keeps its current behaviour.
"""

from django.db import migrations, models

FORWARD = r"""
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "ask_determinism" boolean NOT NULL DEFAULT false;
ALTER TABLE "Exercises" ALTER COLUMN "ask_determinism" DROP DEFAULT;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0010_alter_exercise_exercise_type"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="exercise",
                    name="ask_determinism",
                    field=models.BooleanField(default=False),
                ),
            ],
            database_operations=[
                migrations.RunSQL(FORWARD, migrations.RunSQL.noop)
            ],
        ),
    ]
