"""Additive, idempotent backfill of the teacher-authoring columns.

Prod already had develop's reduced `0001_initial` recorded, so this branch's
squashed `0001_initial` (which creates the full schema) is name-matched as
"already applied" and never runs there — leaving prod without the authoring
columns. This migration adds exactly those columns with ADD COLUMN IF NOT
EXISTS, so it is a no-op on a fresh DB/CI (where 0001 already created them) and
a real backfill on prod. state_operations is empty: 0001 already declares these
fields in Django's model state, so only the database needs reconciling.
"""

from django.db import migrations

FORWARD = r"""
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "image_url" text NULL;
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "kripke_structure" jsonb NULL;
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "allowed_operators" jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "hints" jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "is_published" boolean NOT NULL DEFAULT false;
ALTER TABLE "Exercises" ADD COLUMN IF NOT EXISTS "position" integer NOT NULL DEFAULT 0;
ALTER TABLE "Exercises" ALTER COLUMN "allowed_operators" DROP DEFAULT;
ALTER TABLE "Exercises" ALTER COLUMN "hints" DROP DEFAULT;
ALTER TABLE "Exercises" ALTER COLUMN "is_published" DROP DEFAULT;
ALTER TABLE "Exercises" ALTER COLUMN "position" DROP DEFAULT;

ALTER TABLE "Topics" ADD COLUMN IF NOT EXISTS "visible" boolean NOT NULL DEFAULT true;
ALTER TABLE "Topics" ADD COLUMN IF NOT EXISTS "position" integer NOT NULL DEFAULT 0;
ALTER TABLE "Topics" ADD COLUMN IF NOT EXISTS "unlocks_after_id" uuid NULL;
ALTER TABLE "Topics" ALTER COLUMN "visible" DROP DEFAULT;
ALTER TABLE "Topics" ALTER COLUMN "position" DROP DEFAULT;

ALTER TABLE "Attempts" ADD COLUMN IF NOT EXISTS "hints_used" integer NOT NULL DEFAULT 0;
ALTER TABLE "Attempts" ADD COLUMN IF NOT EXISTS "misconception" varchar(32) NULL;
ALTER TABLE "Attempts" ALTER COLUMN "hints_used" DROP DEFAULT;

CREATE UNIQUE INDEX IF NOT EXISTS "uniq_topic_title_ci" ON "Topics" (LOWER("title"));

DO $$
BEGIN
    -- Only add the self-FK + its index when the column has no FK yet, so a fresh
    -- DB (where 0001 already made a hash-named FK) doesn't get a duplicate.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.conrelid = '"Topics"'::regclass
          AND c.contype = 'f'
          AND a.attname = 'unlocks_after_id'
    ) THEN
        ALTER TABLE "Topics"
            ADD CONSTRAINT "Topics_unlocks_after_id_fk_self"
            FOREIGN KEY ("unlocks_after_id") REFERENCES "Topics" ("id")
            ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
        CREATE INDEX IF NOT EXISTS "Topics_unlocks_after_id_idx" ON "Topics" ("unlocks_after_id");
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # 0001 already declares these fields in model state; reconcile the DB only.
            state_operations=[],
            database_operations=[migrations.RunSQL(FORWARD, migrations.RunSQL.noop)],
        ),
    ]
