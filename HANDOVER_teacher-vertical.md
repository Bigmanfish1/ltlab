# Handover — Teacher pages (mock + vertical backend)

**Date:** 2026-07-05
**Author:** Morgan
**Branch:** `feature/teacher-pages-full` (off `develop`, **not merged**, 25 commits ahead)
**Status:** Teacher functionality complete end-to-end on the real DB. 163 tests pass, ruff clean.
Local seed data present. **Not yet PR'd; migration 0002 not yet deployed to prod.**

> Do NOT commit this file (kept untracked by convention).

---

## What this delivers

Full **teacher** feature, backend-to-frontend, all data reconciling as one unit:
- **Dashboard** (`/`, teacher variant) — enrolled / active-this-week / class accuracy + recent activity.
- **Results** (`/results/`) — metrics, module completion, struggled exercises, misconceptions, roster.
- **Student detail** (`/results/student/<id>/`) — per-student history/accuracy/hints + last submission graph.
- **Manage** (`/teacher/manage/`) — modules list, create (modal) / delete / visibility toggle / drag-reorder,
  nested exercises with per-exercise Edit + delete (themed confirm dialog).
- **Exercise Builder** (`/teacher/exercises/new/`, `/teacher/exercises/<id>/edit/`) — full authoring:
  title, description, difficulty, module, allowed operators, 3 hints, **memorandum Kripke editor**,
  solution formula. **Validate** and **Publish** call the real SPOT engine (`run_ltl_check`); Publish
  is rejected if the formula does not hold on the memorandum. Save Draft skips the holds-check.

Shared/student pages (`exercises`, `exercise_canvas`, `submit_formula`) are **still mock** — out of
scope; they will diverge from real exercises until wired later.

---

## Architecture / key decisions

- **DB conformance.** Prod Supabase already had `Topics`/`Exercises`/`Attempts`/`Users` (teammate-made,
  like `Users`). Django models in `apps/exercises/models.py` conform via `Meta.db_table` + `managed=True`.
  Enums `difficulty`, `user_role` are CharField(choices) (mirrors `Profile.role`). All app FKs CASCADE.
- **Migration split (critical — do not squash):**
  - `0001_initial` = models with **only pre-existing columns** → `--fake-initial` fakes it on prod
    (tables exist), creates them on a fresh local DB.
  - `0002_authoring_fields` = `AddField` for the new authoring columns → **real additive ALTER** on deploy.
  - New columns: Topics `visible`/`position`/`unlocks_after_id`; Exercises `kripke_structure`(jsonb),
    `allowed_operators`(jsonb), `hints`(jsonb), `is_published`, `position`; Attempts `hints_used`.
- **Single-source analytics** — `apps/exercises/services.py`. Every teacher view reads these helpers, so
  numbers reconcile (student detail accuracy == roster row == class contribution; proven in tests).
  Metric defs documented in code: completion = distinct solvers ÷ enrolled; avg_tries = mean
  attempts-to-first-correct; class accuracy = correct ÷ total submissions; avg attempts/ex = total ÷
  engaged (student,exercise) pairs; active-7d = students with an attempt in last 7 days.
- **Misconceptions** — heuristic `classify_misconception(target, submitted)` in services (buckets:
  gf_vs_fg, f_vs_x, safety_vs_liveness, missing_global, nesting_precedence, else english_to_ltl).
  Reported as **share of incorrect submissions** (not % of students — that saturated). safety_vs_liveness
  rarely appears in seed (targets all start with G).
- **Seed** — `manage.py seed_teacher_data` (**DEBUG-only**, refuses under production settings). 40 students,
  5 gated/ordered topics, 36 published exercises (real memorandum Kripke from canonical examples:
  request-grant / traffic / mutex / pulse), ~1900 attempts with `created_at` spread; wrong answers rotate
  through misconception buckets. Local only — prod stays clean. Deterministic (`random.Random(42)`).

---

## Files (most important)

| Area | Path |
|---|---|
| Models | `backend/apps/exercises/models.py` |
| Migrations | `backend/apps/exercises/migrations/0001_initial.py`, `0002_authoring_fields.py` |
| Analytics (single source) | `backend/apps/exercises/services.py` |
| Teacher views + CRUD | `backend/apps/exercises/views.py` (manage, exercise_builder, topic_/exercise_ CRUD) |
| Dashboard/results/student | `backend/apps/home/views.py` |
| Routes | `backend/config/urls.py` |
| Seed | `backend/apps/exercises/management/commands/seed_teacher_data.py` |
| Templates | `backend/templates/manage/teacher_manage.html`, `exercises/teacher_exercise_builder.html`, `results/teacher_results.html`, `results/teacher_student_detail.html`, `dashboard/teacher_dashboard.html` |
| Reusable graph editor | `backend/templates/components/kripke_editor.html` + `backend/static/js/kripke_editor.js` |
| Global toasts | `backend/templates/base.html` |
| Tests | `backend/tests/exercises/` (test_models/test_services/test_views), `backend/tests/home/` |

---

## Gotchas / non-obvious

- **kripke_editor `config.elements` must be a flat ARRAY** (`nodes + edges`), it checks `.length`.
  Passing `{nodes, edges}` falls back to the default demo graph. `_elements_json` (views + services)
  flattens it. Stored `kripke_structure` is the full `getCleanGraphJson()` object `{elements:{nodes,edges},…}`.
- **Toolbar buttons need `type="button"`** — the editor sits inside the builder `<form>`; without it they
  submit the form. Fixed in the component (safe for the formless sandbox).
- **Builder validation UX**: required fields = inline per-field errors (slide/fade, client-side, no layout
  shift); formula-holds failure = fixed bottom-right server toast. Global Django `messages` render as
  auto-dismissing slide-in toasts via `base.html` (success=lime, error=orange).
- **Publish flow**: form posts `formula` + `graph_data` (editor hidden input) + fields; on edit with no
  graph change the hidden input may be empty → view falls back to the existing `kripke_structure`.
- **gcloud** at `/root/google-cloud-sdk/bin` (not on PATH), authed `ltlab700@gmail.com`. Read-only prod
  schema introspection: `gcloud secrets versions access latest --secret=ltlab-database-url` then
  `docker run --rm postgres:16 psql "$DBURL" -c "…"`.
- Repo convention: **no explanatory comments in code**; **no `Co-Authored-By` trailers**.

---

## How to run / verify

```bash
docker compose up
docker exec ltlab-web python manage.py migrate            # fresh local: applies 0001 + 0002
docker exec ltlab-web python manage.py seed_teacher_data  # coherent local dataset (DEBUG only)
docker exec ltlab-web python manage.py set_role <email> teacher
docker exec ltlab-web python manage.py test               # 163 pass
docker exec ltlab-web ruff check .                        # clean
```
Sign in as teacher; click through: dashboard ↔ results ↔ a student's detail ↔ manage numbers agree;
create/edit/publish an exercise (Validate/Publish hit the engine; edited Kripke reloads correctly);
toggle visibility / reorder / delete persist across reload.

---

## What's NOT done — next steps

1. **PR `feature/teacher-pages-full` → `develop`.** (Also flag to schema owner: 0002 is additive/
   non-breaking but ALTERs the shared Supabase tables on deploy.)
2. **Deploy runs 0002** on prod via the `ltlab-migrate` Job — prod tables gain the new columns then.
   Prod teacher pages are empty until a real teacher authors content (seed is local-only, by design).
3. **Wire the student side** (later scope): `submit_formula` should persist real `Attempt`s and
   `exercises`/`exercise_canvas` should read real published exercises — then the whole app is coherent,
   not just the teacher half.
4. **Exercise reordering** within a module not wired (topic drag-reorder is: `topic_reorder`). Add an
   exercise-reorder endpoint + drag if wanted.
5. `safety_vs_liveness` misconception rarely surfaces in seed — add a "drop-F" wrong variant if a fuller
   spread is wanted.
6. Optional: unify the two toast systems (global `messages` 4s vs builder server-error 6s) into one.

---

## Commits (25) — recent tail
```
fe9c417 fix(builder): reload saved Kripke structure instead of default demo
8d4c803 fix(kripke-editor): type=button on toolbar so it doesn't submit a wrapping form
69885a1 fix(ui): render Django flash messages as auto-dismissing slide-in toasts
9acec90 feat(manage): themed confirmation modal for module/exercise deletion
5524ca9 feat(builder): preselect module when adding an exercise from a module card
4e5174b fix(builder): animate validation errors (slide/fade in and out)
75d4b44 fix(builder): inline field-level validation errors + non-shifting server toast
1aaa532 test(exercises): publish-validation, gating, CRUD, reconciliation, conformance
105364e feat(exercises): coherent seed_teacher_data command (local-only) + tuned analytics
b0b9564 feat(builder): real save form + memorandum reload; wire student-detail last submission
598907e feat(teacher): real ORM views + topic/exercise CRUD, publish via run_ltl_check
de34245 feat(exercises): analytics services (single source, misconception classifier)
ce30bb8 feat(exercises): Topic/Exercise/Attempt models + conform/authoring migrations
… (12 earlier: mock phase — nav, manage, builder, student-detail, create-module modal, fixes)
```
