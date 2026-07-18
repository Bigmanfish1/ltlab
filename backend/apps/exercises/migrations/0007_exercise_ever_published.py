"""ever_published flag on Exercises (teammate-owned table — 0005's shape).

The backfill marks currently-published rows; a row that was published and
later re-drafted before this deploy stays false, which the type-change gate
covers with an attempts-exist check.
"""

from django.db import migrations, models

FORWARD = r"""
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "ever_published" boolean NOT NULL DEFAULT false;
UPDATE "Exercises" SET ever_published = true WHERE is_published;
ALTER TABLE "Exercises" ALTER COLUMN "ever_published" DROP DEFAULT;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0006_exercisepart_hints"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="exercise",
                    name="ever_published",
                    field=models.BooleanField(default=False),
                ),
            ],
            database_operations=[
                migrations.RunSQL(FORWARD, migrations.RunSQL.noop)
            ],
        ),
    ]
