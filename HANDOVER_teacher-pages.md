# Handover — Teacher Pages (`feature/teacher-pages`)

**Date:** 2026-06-16
**Author:** Morgan
**Branch:** `feature/teacher-pages` (off `develop`, pushed to origin)
**Status:** Complete, mock-data only, ready for PR review. Not yet merged.

---

## What this delivers

Two teacher-only pages, gated by `@teacher_required`:

| Page | URL | Template | View |
|---|---|---|---|
| Exercise management | `/teacher/exercises/` | `templates/exercises/teacher_exercises.html` | `teacher_exercises` in `apps/exercises/views.py` |
| Class analytics | `/results/` | `templates/results/teacher_results.html` | `teacher_results` in `apps/home/views.py` |

Built from Figma exports (`admin_excercises.png`, `admin_dashboardresults.png`, `admin_dashboard_continued.png`). All data is mock — **no DB models, no backend persistence**.

### Navigation (`templates/header.html`)
- **Exercises** link is role-aware: teachers → `/teacher/exercises/`, students → `/exercises/` (student page unchanged).
- **Results** link visible to all, but `href="#"` for non-teachers (page not built for students yet); teachers get the real `/results/`.

---

## Interactivity (frontend-only, vanilla JS)

Scoped to controls shown in the design; small inline `<script>` per template, no libraries.

**`teacher_exercises.html`:**
- Difficulty filter tabs (All/Beginner/Intermediate/Advanced)
- Search (name + module), combines with active tab
- Client-side pagination (`PAGE_SIZE = 6`)
- Delete icon removes row — **ephemeral, resets on reload** (no backend)
- `New Exercise` + edit icon are inert (need backend)

**`teacher_results.html`:**
- Student search (name)
- `View →` per student is inert (need backend)

> Rationale: client-side is correct at this scale (~20 rows; threshold for server-side is hundreds-to-thousands). Code comments note these move to backend queries when real data scales.

---

## Mock data locations
- `MOCK_TEACHER_EXERCISES` — `apps/exercises/views.py` (20 entries, each has `difficulty`)
- `_MOCK_RESULTS_DATA` — `apps/home/views.py` (metrics, module_completion, struggled_exercises, misconceptions, students)

---

## What's NOT done (next steps for backend owner)
1. **DB models** — exercises, attempts, student progress. Replace both mock dicts with ORM queries.
2. **Real actions** — wire `New Exercise`, edit, delete (currently inert/ephemeral).
3. **Student `View →`** — student detail page does not exist.
4. **Server-side search/filter/pagination** once datasets grow past a few hundred rows.
5. **Student Results page** — Results nav is `#` for students by design; build when ready.
6. **No tests** — these are mock UI pages; add view/template tests when models land.

---

## How to run / verify
```bash
docker compose up                                   # start stack
docker exec ltlab-web python manage.py set_role <email> teacher   # become a teacher
```
Then sign in (Google OAuth) and visit `/teacher/exercises/` and `/results/`.
- Teacher: both pages load; nav highlights correctly.
- Student: Exercises → student list; Results link does nothing.

Lint: `docker exec ltlab-web ruff check .` (passes).

---

## Review note
Ran `/code-review` (high effort) against `develop`. 4 findings, all fixed in commits `7fdbd8c`, `1357099`, `5f3455c`, `b2b688d`:
1. Results link silently redirected non-teachers → now inert `#`.
2. Inconsistent DOM-query pattern between the two templates → both re-query now.
3. `teacher_results` was misplaced in `exercises` app → moved to `home` (alongside `teacher_dashboard`).
4. Filter-tab active state defined in two places → single source via `setActive()` helper.

One finding (module-level mock dict mutation) was REFUTED — Django 5.x doesn't mutate the context dict.

---

## Commits (8)
```
feat(exercises): add teacher exercise and results views with mock data
feat(header): wire Results link and role-aware Exercises nav for teachers
feat(templates): add teacher exercise management page
feat(templates): add teacher analytics results page
fix(header): make Results link inert for non-teachers
fix(results): re-query student rows per search instead of caching once
refactor: move teacher_results analytics view to home app
refactor(exercises): single source of truth for filter tab active state
```
