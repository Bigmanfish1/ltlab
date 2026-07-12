"""Align Topics.created_by column name with prod.

This branch's Topic model carried db_column="created_by" (from the old
teammate int8 schema), but prod was rebuilt by develop with the Django default
name `created_by_id`. Dropping db_column makes the model use `created_by_id`;
this migration renames the column on any DB that still has `created_by`
(fresh/local, where 0001 created it) and is a no-op where it is already
`created_by_id` (prod). state_operations updates the field to match the model
so makemigrations detects no drift.
"""

import django.db.models.deletion
from django.db import migrations, models

RENAME = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'Topics' AND column_name = 'created_by'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'Topics' AND column_name = 'created_by_id'
    ) THEN
        ALTER TABLE "Topics" RENAME COLUMN "created_by" TO "created_by_id";
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0002_authoring_fields"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="topic",
                    name="created_by",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="topics",
                        to="accounts.profile",
                    ),
                ),
            ],
            database_operations=[migrations.RunSQL(RENAME, migrations.RunSQL.noop)],
        ),
    ]
