# HANDOVER — Load testing & DB performance (2026-07-01)

Session focus: empirically load-test the Cloud Run deployment, find why it failed
under load, fix it, and verify. **Do not commit this file** (keep untracked).

## TL;DR outcome
- Proved Cloud Run autoscaling works; the LTL/SPOT engine is **not** the bottleneck.
- Found + fixed the real failure: Supabase **session pooler 15-connection cap**
  (`EMAXCONNSESSION`) → switched to the **transaction pooler (:6543)**.
- Fixed a latent bug: `DISABLE_SERVER_SIDE_CURSORS` was dead (module-level); moved
  into `DATABASES["default"]`. Added a prod `LOGGING` config (500s were invisible).
- All committed + pushed on `feature/gcp-cloud-run-migration`. Prod = revision
  `ltlab-00012-5p6`.

## Current prod state
- Service `ltlab`, region `us-central1`, project `project-10a4cc96-5dd3-4010-8a3`.
- `DATABASE_URL` = Supabase **transaction pooler, port 6543** (set via
  `gcloud run --update-env-vars`; also documented in .env.example/bootstrap.sh/CLAUDE.md/README).
- Settings: `DISABLE_SERVER_SIDE_CURSORS` in `DATABASES["default"]`, prod `LOGGING`
  (django.request ERROR → stderr, DEBUG stays False).

## Commits this session (all pushed)
```
672a21d docs: correct README DATABASE_URL to transaction pooler :6543
6f32228 fix(settings): move DISABLE_SERVER_SIDE_CURSORS into DATABASES so it applies
2773e32 docs(infra): record transaction pooler :6543 as the prod DB URL
73aceeb chore(settings): drop stale Render references from production settings
120bdff feat(settings): log request errors to stderr in production
3d51faa fix(settings): disable server-side cursors for transaction-mode pooler
```
Working tree clean re: this work (middleware.py netted to zero — a Profile cache was
added then removed; per-instance LocMemCache doesn't help realistically).

## Key empirical findings
Three-level latency decomposition (~15-state Kripke graphs, 1→200 users, 0 errors):
- **Engine only** (in-process, 400 runs): median **0.44 ms**, max 31 ms. SPOT is sub-ms.
- **Server-side** (Cloud Run request latency from logs): median **~800 ms FLAT** from
  1→200 users, instances scaled 1→20, 0 5xx. Perfect horizontal scaling.
- **Client end-to-end** (single WSL box): median 1516→2456 ms; the rise at 200 users
  is the single-box client ceiling (~85 rps), NOT the server.
- **The ~800 ms server floor is the per-request DB path**: `CONN_MAX_AGE=0` (Django
  default, still unset) opens a NEW connection to the Frankfurt pooler every request
  while the app runs in Iowa. Cross-Atlantic connect dominates; engine is 0.44 ms.
- Before the fix: session pooler (:5432) 500'd from ~50 users (24% at 200) with
  `OperationalError: (EMAXCONNSESSION) max clients reached in session mode - pool_size: 15`.
  After (:6543): 0 errors through 200 users.

## Next steps (ranked)
1. **`CONN_MAX_AGE` > 0** — reuse DB connections; should cut the ~800 ms server floor
   toward one RTT (~150 ms). Safe with the transaction pooler. A/B rerun to confirm.
2. **Co-locate DB + app region** (Cloud Run europe-west, or DB near app) — kills the
   cross-Atlantic tax.
3. Outstanding /code-review items (lower severity, not yet done):
   - `start-web.sh`: no per-check timeout; gunicorn `--timeout 30` on 1 worker kills the
     whole worker (all 8 threads) on a pathological SPOT run. Isolation regression vs Celery.
   - `.github/workflows/ci.yml`: build poll treats `STATUS_UNKNOWN` as terminal → possible
     flaky deploy; migrate step `gcloud run jobs update` has no existence check.
   - `backend/templates/sandbox/sandbox.html:98`: redundant `hx-on:htmx:response-error`
     (after-request already covers error) — double-fires enableRunBtn().
   - `engine.py:173`: stale "Celery task" docstring (out of diff scope).

## How to re-run the load test
Scripts in scratchpad (NOT in repo): `ltl_loadtest.py` (stdlib) + `ltl_loadtest.js` (k6).
- Needs a fresh `sb-access-token` JWT (expires ~1h): browser DevTools → Application →
  Cookies → copy `sb-access-token`, save to `scratchpad/.cookie` (JWT only).
- `python3 ltl_loadtest.py smoke` then `python3 ltl_loadtest.py staged 30`
  (phases 1,5,10,30,50,100,200). Reuses ONE cookie across all workers (JWT verified
  locally, so one cookie == many users). Randomizes ~15-state graphs per request
  (cache-miss → real SPOT). For 100/200 users trust k6 from a cloud box (single WSL
  box caps ~85 rps).
- Engine-only micro-benchmark: `docker exec -i ltlab-web python - <<'PY' ... PY`
  importing `apps.checker.tasks.run_ltl_check` (see session; measures pure engine ms).

## Gotchas
- **Cookie** expires ~1h; smoke shows `AUTH_FAIL` (200 + login page) when stale.
- **GCP MCP servers** (gcp-cloud-run/logging/monitoring) auth via a static bearer token
  in `~/.claude.json` headers that expires ~1h. Refresh: `~/.claude/gcp-mcp-refresh.sh`
  then `/mcp` reconnect (or restart). Alternatively just use `gcloud` CLI directly (works
  as long as `ltlab700@gmail.com` creds are valid) — that's what most of this session used.
- **Server-side latency** best pulled from request logs (`httpRequest.latency`) bucketed
  by phase; the monitoring metric's 60s alignment is too coarse for 30s phases.
- Prod secrets (DB password, SECRET_KEY) got printed into the session transcript via
  `get_service` — rotate if that transcript leaks.

## Memory
Full detail persisted in the auto-memory: `project_loadtest_findings.md` (indexed in
MEMORY.md). A fresh session will load the index automatically.
