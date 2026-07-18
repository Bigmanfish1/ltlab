"""Exercise-type discriminator, sub-question parts, and per-part attempt answers.

ExerciseParts is a brand-new table, so a plain CreateModel is safe on prod.
The four columns land on teammate-owned tables ("Exercises", "Attempts"), so
they follow 0002's shape: state_operations declare the fields to Django while
the database side is idempotent ADD COLUMN IF NOT EXISTS — the DDL default
backfills existing prod rows, then the default is dropped to match Django's
no-DB-default convention. The part_id FK/index is guarded the same way as
0002's unlocks_after_id block.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models

FORWARD = r"""
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "exercise_type" varchar(20) NOT NULL DEFAULT 'model_check';
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "declared_aps" jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE "Exercises" ALTER COLUMN "exercise_type" DROP DEFAULT;
ALTER TABLE "Exercises" ALTER COLUMN "declared_aps" DROP DEFAULT;

ALTER TABLE "Attempts" ADD COLUMN IF NOT EXISTS "part_id" uuid NULL;
ALTER TABLE "Attempts" ADD COLUMN IF NOT EXISTS "answer" jsonb NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.conrelid = '"Attempts"'::regclass
          AND c.contype = 'f'
          AND a.attname = 'part_id'
    ) THEN
        ALTER TABLE "Attempts"
            ADD CONSTRAINT "Attempts_part_id_fk_exerciseparts"
            FOREIGN KEY ("part_id") REFERENCES "ExerciseParts" ("id")
            ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
        CREATE INDEX IF NOT EXISTS "Attempts_part_id_idx" ON "Attempts" ("part_id");
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0004_alter_exercise_target_formula"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExercisePart",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("position", models.IntegerField(default=0)),
                ("prompt", models.TextField(blank=True, default="")),
                ("formula", models.TextField()),
                (
                    "exercise",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="parts",
                        to="exercises.exercise",
                    ),
                ),
            ],
            options={
                "db_table": "ExerciseParts",
                "ordering": ["position", "id"],
            },
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="exercise",
                    name="exercise_type",
                    field=models.CharField(
                        choices=[
                            ("model_check", "Write a formula that holds"),
                            ("judge", "Judge formulas + counterexample"),
                            ("path_exhibit", "Exhibit a satisfying path"),
                            ("english_to_formula", "English requirement to formula"),
                        ],
                        default="model_check",
                        max_length=20,
                    ),
                ),
                migrations.AddField(
                    model_name="exercise",
                    name="declared_aps",
                    field=models.JSONField(blank=True, default=list),
                ),
                migrations.AddField(
                    model_name="attempt",
                    name="part",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attempts",
                        to="exercises.exercisepart",
                    ),
                ),
                migrations.AddField(
                    model_name="attempt",
                    name="answer",
                    field=models.JSONField(blank=True, null=True),
                ),
            ],
            database_operations=[
                migrations.RunSQL(FORWARD, migrations.RunSQL.noop)
            ],
        ),
    ]
