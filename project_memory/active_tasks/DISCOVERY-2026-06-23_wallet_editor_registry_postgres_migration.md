# Discovery — WalletEditor Registry: Excel → PostgreSQL (Railway)

| Мета | Значение |
|------|----------|
| **Тип** | Investigation only (no code changes) |
| **Дата** | 2026-06-23 |
| **Цель** | Заменить `wallet_editor.xlsx` как source of truth на Postgres без поломки disable / add / edit / auto-enable / outbox / replay flows |
| **Статус KB** | CONFIRMED facts from repo; live prod Postgres — UNKNOWN |

---

## 1. Current registry architecture

### 1.1 Source of truth today

| Layer | Location | Role |
|-------|----------|------|
| **Primary registry** | Dropbox `{DROPBOX_WALLET_EDITOR_PATH}` → `wallet_editor.xlsx` | Cumulative history: `all_results`, `runs`, operator-managed `hold`, `Отлёжка` |
| **Durability / replay** | `{STATE_DIR}/wallet_editor/` | Outbox, durable result copies, processed run IDs, warned partners |
| **Per-run results** | `/tmp/wallet_editor/*.xlsx` | Ephemeral disable/add/edit outputs; only **disable** path feeds registry |

`STATE_DIR` default `/data/state` (Railway Volume). Postgres in codebase **отсутствует** (`tasks.md` WE-POSTGRES-HISTORY = OPTIONAL future).

### 1.2 Write model (critical)

Все мутации registry — **optimistic concurrency через Dropbox rev**:

```
download_file_with_rev → mutate local xlsx (openpyxl) → upload_file_if_rev(expected_rev)
```

- In-process `threading.Lock` (`wallet_editor_registry._lock`) — shared с `registry_refresh.py`
- Retry loop до `registry_timeout_seconds` (Rules `job_params` `wallet_editor`)
- Rev conflict → retry, не блокирует Telegram delivery per-run result

### 1.3 Sheet ownership

| Sheet | Written by code? | Read by code? | Notes |
|-------|------------------|---------------|-------|
| `all_results` | **Yes** — append, patch, lifecycle refresh | Yes | Full recalc `recalculate_all_results()` on every append/patch |
| `runs` | **Yes** — append only | Yes (legacy `run_id` dedup) | One row per disable run |
| `hold` | **No** (headers only if missing) | Yes | Operator-managed; runtime enforcement via `wallet_editor_hold.py` |
| `Отлёжка` | **No** (headers only if missing) | Yes | Partner → `Полные дни`; drives `Дата включения` |

### 1.4 Auxiliary STATE_DIR files (not in xlsx)

| Path | Purpose |
|------|---------|
| `wallet_editor/outbox/index.json` | Outbox records: `pending` → `syncing` → `synced` / `failed` |
| `wallet_editor/results/{run_id}.xlsx` | Durable per-run result for replay |
| `wallet_editor/registry_processed_run_ids.json` | Idempotency + repair detection |
| `wallet_editor/missing_hold_days_warned.json` | One-time TG warning per partner missing on `Отлёжка` |

### 1.5 Runtime flows touching registry

```
                    ┌─────────────────────────────────────────┐
                    │     Dropbox wallet_editor.xlsx          │
                    │  all_results | runs | hold | Отлёжка    │
                    └──────────────▲──────────────────────────┘
                                   │ download/upload + rev
     ┌─────────────────────────────┼─────────────────────────────┐
     │                             │                             │
 manual TG xlsx          conversion → WE bridge          auto-enable B2
 (disable)              (disable via worker)             (patch Включено)
     │                             │                             │
     ▼                             ▼                             ▼
 worker._run_disable_task    same path                   patch_enable_results_in_dropbox_registry
     │                             │
     └──────── prepare_registry_outbox_and_schedule ────────┘
                    │
                    ▼ async thread: append_run_to_dropbox_registry
                    │
     add_wallet / edit_wallet ──► NO registry (E-WE-19)
     auto-enable plan (A) ────────► READ only
     hold check (engine/executor) ► READ hold sheet only
     registry_refresh job ───────► READ + recalc lifecycle + conditional upload
     /registry_health, /registry_replay ► outbox + optional registry read
```

---

## 2. Read/write map by module/function

### 2.1 Core registry (`integrations/wallet_editor_registry.py`)

| Function | R/W | Sheets / data | Callers |
|----------|-----|---------------|---------|
| `wallet_editor_dropbox_path()` | — | env `DROPBOX_WALLET_EDITOR_PATH` | all registry paths |
| `append_run_to_dropbox_registry` | **W** | `all_results` (+recalc), `runs` | `registry_async.schedule_registry_append`, `replay_pending_outbox_records` |
| `_append_attempt` | **W** | download → merge rows → recalc → save → upload | internal |
| `patch_enable_results_in_dropbox_registry` | **W** | patch `Включено`, `Комментарий включения` on `all_results` + recalc | `wallet_editor_auto_enable._run_phase_b2_batches` |
| `_patch_attempt` | **W** | same | internal |
| `apply_enable_updates_to_all_results` | transform | in-memory only | patch path |
| `build_registry_health_report` | **R** | outbox + download `all_results` + recalc metrics | `/registry_health`, `registry_stale_outbox_warning` |
| `replay_pending_outbox_records` | **W** (via append) | replays outbox → append | `/registry_replay`, job `wallet_editor_registry_replay` |
| `run_registry_outbox_replay_job` | **W** | wrapper | `JOB_REGISTRY`, `tg_commands` |

**Lock:** `_lock` serializes append, patch, refresh within one process.

### 2.2 XLSX I/O (`integrations/wallet_editor_registry_xlsx.py`)

| Function | R/W | Notes |
|----------|-----|-------|
| `load_registry_frames` | **R** | Returns 4 DataFrames + hold/otlezka existence flags |
| `read_all_results_ws` | **R** | Legacy migration, `card` as text |
| `read_runs_ws` | **R** | Legacy `run_id` column support |
| `read_user_sheet_ws` | **R** | `hold`, `Отлёжка` |
| `save_registry_workbook` | **W** | **Only** syncs `all_results` + `runs`; preserves hold/otlezka bytes |
| `card_as_text` | — | Used when reading result xlsx |

### 2.3 Lifecycle (`integrations/wallet_editor_registry_lifecycle.py`)

| Function | R/W | Notes |
|----------|-----|-------|
| `recalculate_all_results` | transform | **Pure** — derives `Дата включения`, `Статус включения`, `hold` from hold+otlezka |
| `rows_from_result_excel` | transform | Per-run result → `all_results` rows |
| `build_runs_row` | transform | Stats → `runs` row |
| `run_id_already_processed` | **R** | `processed_run_ids` + fingerprint repair logic |
| `mark_run_processed` / `unmark_run_processed` | **W** | STATE_DIR JSON |
| `result_row_fingerprint` | — | Dedup key: card\|partner\|action\|disable_date\|status\|comment |
| `load_hold_pairs` / `load_otlezka_days` | transform | From DataFrames |
| `load/save_warned_partners` | **R/W** | STATE_DIR JSON |

### 2.4 Async / outbox (`integrations/wallet_editor_registry_async.py`)

| Function | R/W | Notes |
|----------|-----|-------|
| `prepare_registry_outbox_and_schedule` | **W** outbox + schedule | Called from `worker._run_disable_task` **after** TG send |
| `persist_durable_result_copy` | **W** | `{STATE_DIR}/wallet_editor/results/{run_id}.xlsx` |
| `create_outbox_record` / `update_outbox_status` | **R/W** | `outbox/index.json` |
| `schedule_registry_append` | schedules | Daemon thread → `append_run_to_dropbox_registry` |

### 2.5 Lifecycle refresh (`integrations/wallet_editor_registry_refresh.py`)

| Function | R/W | Notes |
|----------|-----|-------|
| `refresh_wallet_editor_registry_lifecycle` | **R+conditional W** | Daily/manual; upload only if lifecycle columns changed |
| `run_wallet_editor_registry_refresh_job` | — | `JOB_REGISTRY`, `/wallet_editor_refresh` |

### 2.6 Hold enforcement (`integrations/wallet_editor_hold.py`)

| Function | R/W | Notes |
|----------|-----|-------|
| `load_hold_pairs_from_dropbox` | **R** | Downloads full workbook; reads `hold` sheet only |

### 2.7 Auto-enable (`integrations/wallet_editor_auto_enable*.py`)

| Module | Function | R/W | Notes |
|--------|----------|-----|-------|
| `auto_enable.py` | `load_registry_frames_for_planning` | **R** | Download + recalc for plan/run |
| `auto_enable.py` | `_run_phase_b2_batches` | **W** via patch | After Antares batch |
| `auto_enable.py` | `run_auto_enable_plan` | **R** | Plan-only |
| `auto_enable.py` | `run_auto_enable` | **R** + optional **W** | Phase B2 |
| `auto_enable_eligibility.py` | `select_auto_enable_candidates` | transform | In-memory on recalculated `all_results` |
| `auto_enable_executor.py` | `load_hold_pairs_from_dropbox` | **R** | Per batch, separate download |

### 2.8 Worker (`automation/worker.py`)

| Task handler | Registry? |
|--------------|-----------|
| `_run_disable_task` | **Yes** — `prepare_registry_outbox_and_schedule` |
| `_run_add_wallet_task` | **No** (E-WE-19) |
| `_run_edit_wallet_task` | **No** |
| `_run_auto_enable_batch_task` | **No** directly; patch in orchestrator after batch |

### 2.9 Telegram (`integrations/tg_commands.py`)

| Command / job | Registry interaction |
|---------------|---------------------|
| `/registry_health` | `build_registry_health_report` |
| `/registry_replay` | `replay_pending_outbox_records` |
| `/wallet_editor_refresh` | job `wallet_editor_registry_refresh` |
| `/auto_enable_plan`, `/auto_enable_run` | via `wallet_editor_auto_enable` |

### 2.10 Conversion bridge (`integrations/conversion_wallet_editor_bridge.py`)

Queues `WalletEditorTask` → `add_task` → same disable worker path → registry append.

---

## 3. Sheets and columns (canonical)

### 3.1 `all_results` (program-owned)

| Column | Source on append | Recalculated | Patched by auto-enable |
|--------|------------------|--------------|------------------------|
| `Дата операции` | result xlsx | — | — |
| `Дата отключения` | result xlsx | — | match key |
| `Дата включения` | — | **yes** (otlezka + disable) | — |
| `Статус включения` | — | **yes** | — |
| `Включено` | — | drives status if set | **yes** (OK/SKIP/FAIL) |
| `Комментарий включения` | — | — | **yes** |
| `card` | result xlsx | — | match key |
| `partner` | derived from `value` | recalc ensures | match key |
| `action` | result xlsx | — | must be `remove_partner` for patch |
| `status` | result xlsx | — | — |
| `comment` | result xlsx | — | — |
| `hold` | — | **yes** (from hold sheet) | — |

**Dedup fingerprint** (repair/idempotency): `card|partner|action|Дата отключения|status|comment` (normalized).

### 3.2 `runs` (program-owned)

`started_at`, `finished_at`, `input_rows`, `success_rows`, `failed_rows`, `skipped_rows`, `output_file`

Legacy columns (`run_id`, `operator_profile`, …) migrated on read; not written in current schema.

### 3.3 `hold` (operator-owned)

`Дата добавления`, `card`, `partner`, `comment` — **read-only for code**.

### 3.4 `Отлёжка` (operator-owned)

`partner`, `Полные дни`, `comment` — **read-only for code**.

---

## 4. Proposed Postgres schema draft

Design principles: mirror current semantics; DB becomes SoT in Phase 2; Excel export is projection in Phase 3.

### 4.1 Core tables

```sql
-- Operator-managed reference data (replaces hold / Отлёжка sheets)
CREATE TABLE we_registry_hold (
    id              BIGSERIAL PRIMARY KEY,
    card_norm       TEXT NOT NULL,
    partner_norm    TEXT NOT NULL,
    card            TEXT NOT NULL,
    partner         TEXT NOT NULL,
    added_at        DATE,
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (card_norm, partner_norm)
);

CREATE TABLE we_registry_otlezka (
    id              BIGSERIAL PRIMARY KEY,
    partner_norm    TEXT NOT NULL UNIQUE,
    partner         TEXT NOT NULL,
    full_days       INTEGER NOT NULL CHECK (full_days >= 0),
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per disable run (replaces runs sheet)
CREATE TABLE we_registry_runs (
    run_id              UUID PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ NOT NULL,
    input_rows          INTEGER NOT NULL,
    success_rows        INTEGER NOT NULL,
    failed_rows         INTEGER NOT NULL,
    skipped_rows        INTEGER NOT NULL,
    output_file         TEXT,
    operator_profile    TEXT,
    source              TEXT,  -- telegram_manual | conversion_auto
    telegram_chat_id    BIGINT,
    telegram_user_id    BIGINT,
    source_file_name    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cumulative result rows (replaces all_results sheet)
CREATE TABLE we_registry_results (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID REFERENCES we_registry_runs(run_id),
    operation_date      DATE,
    disable_at          TIMESTAMPTZ,
    reenable_date       DATE,
    enable_status       TEXT,       -- Статус включения enum text
    vklyucheno          TEXT,       -- OK | SKIP | FAIL | ''
    enable_comment      TEXT,
    card                TEXT NOT NULL,
    card_norm           TEXT NOT NULL,
    partner             TEXT NOT NULL,
    partner_norm        TEXT NOT NULL,
    action              TEXT NOT NULL,
    status              TEXT NOT NULL,
    comment             TEXT,
    hold_mark           TEXT,       -- '' | HOLD
    row_fingerprint     TEXT NOT NULL,
    source_row_index    INTEGER,    -- for auto-enable patch targeting
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX we_registry_results_fingerprint_uidx
    ON we_registry_results (row_fingerprint);

CREATE INDEX we_registry_results_card_partner_idx
    ON we_registry_results (card_norm, partner_norm);

CREATE INDEX we_registry_results_enable_status_idx
    ON we_registry_results (enable_status)
    WHERE action = 'remove_partner' AND status = 'OK';
```

### 4.2 Outbox / durability (migrate from STATE_DIR JSON)

```sql
CREATE TABLE we_registry_outbox (
    run_id              UUID PRIMARY KEY REFERENCES we_registry_runs(run_id),
    status              TEXT NOT NULL CHECK (status IN ('pending','syncing','synced','failed')),
    attempts            INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    last_attempt_at     TIMESTAMPTZ,
    registry_synced_at  TIMESTAMPTZ,
    durable_result_path TEXT,  -- optional until blob storage
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE we_registry_processed_runs (
    run_id              UUID PRIMARY KEY,
    processed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE we_registry_warned_partners (
    partner_norm        TEXT PRIMARY KEY,
    warned_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.3 Concurrency (replaces Dropbox rev)

```sql
CREATE TABLE we_registry_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- e.g. schema_version, last_excel_export_rev, migration_phase
```

Use **transaction-level row locks** or `SELECT … FOR UPDATE` on `we_registry_meta` + monotonic `registry_version` incremented per mutation batch (append / patch / refresh). Replaces optimistic Dropbox rev.

### 4.4 Lifecycle computation

**Recommendation:** keep `recalculate_all_results()` as **pure Python** in Phase 1–2; call it after reads (as today) until Phase 2b adds DB-side materialized columns or triggers. Avoid changing eligibility logic during migration.

Optional Phase 2b: store `reenable_date` / `enable_status` / `hold_mark` as **materialized** columns updated by same `recalculate_all_results` in application code (not DB triggers) — preserves E-WE-08 semantics.

---

## 5. Migration phases

### Phase 1 — DB mirror (dual-write optional, read still Excel)

| Step | Action |
|------|--------|
| 1.1 | Provision Railway Postgres; `DATABASE_URL`; no runtime dependency until flag on |
| 1.2 | One-time **import** from current Dropbox xlsx → Postgres tables |
| 1.3 | Add **shadow writer**: after successful Dropbox upload, upsert same mutation to Postgres (best-effort, logged) |
| 1.4 | Add **reconciliation job**: compare row counts, fingerprints, hold/otlezka sets Excel vs DB; TG alert on drift |
| 1.5 | Keep outbox / processed_run_ids in STATE_DIR unchanged |

**Exit criteria:** N days shadow with zero drift; reconciliation green.

### Phase 2 — DB source of truth

| Step | Action |
|------|--------|
| 2.1 | Feature flag `WALLET_EDITOR_REGISTRY_SOURCE=postgres|dropbox` (default `dropbox`) |
| 2.2 | Refactor `wallet_editor_registry.py` behind `RegistryStore` interface: `load_frames()`, `append_run()`, `patch_enable()`, `refresh_lifecycle()` |
| 2.3 | Postgres implementation: single transaction per append/patch/refresh; `FOR UPDATE` on meta version |
| 2.4 | `wallet_editor_hold.py` reads hold from DB when flag on |
| 2.5 | Auto-enable planning reads recalculated frames from DB |
| 2.6 | Outbox can remain STATE_DIR initially OR migrate to `we_registry_outbox` with same status semantics |
| 2.7 | **Stop Dropbox upload** when flag `postgres`; keep download-only for rollback window |

**Exit criteria:** prod on `postgres` 2+ weeks; `/registry_health` green; auto-enable B2 verified.

### Phase 3 — Excel export only

| Step | Action |
|------|--------|
| 3.1 | Scheduled job: Postgres → `wallet_editor.xlsx` (openpyxl, preserve UX-A formatting) → Dropbox upload |
| 3.2 | `hold` / `Отлёжка` edits: either TG admin commands → DB, or import-from-Excel sidecar (read Excel diff → upsert DB) until UI exists |
| 3.3 | Remove Dropbox rev conflict retry loops from hot path |
| 3.4 | Deprecate `registry_processed_run_ids.json` when outbox fully in DB |

---

## 6. Compatibility plan with current Excel

| Concern | Plan |
|---------|------|
| Operators edit `hold` / `Отлёжка` in Excel | Phase 1–2: continue Excel ownership; optional **import webhook** or daily sync Excel→DB for those sheets. Phase 3: move editing to DB admin path + export |
| Formatting (UX-A) | DB stores data only; export job reproduces openpyxl styling from template workbook |
| `card` as text | `TEXT` column + norm index; never numeric cast |
| Legacy `run_id` in runs sheet | Import maps to `we_registry_runs`; dedup uses `we_registry_processed_runs` |
| Fingerprint / repair | Keep same `result_row_fingerprint` algorithm; repair = re-insert missing fingerprints for processed run_id |
| Rules `job_params` timeouts | Unchanged semantics; retries become DB deadlock/serialization retries instead of rev conflict |
| E-WE-19 add_wallet no registry | Unchanged |
| Conversion → WE | Unchanged worker path; only store backend changes |

---

## 7. Rollback plan

| Phase | Rollback |
|-------|----------|
| **Phase 1** | Disable shadow writer; no prod impact |
| **Phase 2** | Set `WALLET_EDITOR_REGISTRY_SOURCE=dropbox`; redeploy previous build if needed. **Requirement:** keep Dropbox xlsx updated during Phase 2 parallel period OR freeze writes before cutover |
| **Phase 2 emergency** | Re-export Postgres → xlsx, upload Dropbox, flip flag to dropbox |
| **Phase 3** | Re-enable Phase 2 dual path; Excel export job can be stopped independently |

**Data backup:** Railway Postgres automated backups + before cutover manual xlsx download from Dropbox.

**Do not rollback** by deleting Postgres data until Excel proven current (run reconciliation in reverse).

---

## 8. Migration risks

| Risk | Current mitigation | Postgres migration note |
|------|-------------------|---------------------------|
| **Concurrent writes** | In-process `_lock` + Dropbox rev | Need DB transactions + advisory lock or version column; multi-instance Railway replicas = **new risk** (today single scheduler process helps) |
| **Railway restart** | STATE_DIR Volume persists outbox/results | Postgres survives; `/tmp` auth-state still ephemeral (unchanged) |
| **Dropbox conflict** | rev check + retry | Eliminated for DB SoT; reappears only on export job (use export rev or separate path) |
| **Duplicate rows** | `processed_run_ids` + fingerprint + `run_id_already_processed` | UNIQUE on `row_fingerprint`; `run_id` PK on runs; replay idempotent |
| **Lifecycle recalculation** | Full recompute on every append | Must run same function on same inputs; changing store must not change column order/types |
| **Restored workbook trap** | repair re-append when processed but fingerprints missing | Reconciliation job + replay from durable `results/{run_id}.xlsx` |
| **hold/otlezka drift** | Manual Excel edits | Biggest operational risk; need sync strategy before Phase 3 |
| **Auto-enable patch wrong row** | Match card+partner+disable_date+source_row_index | Preserve `_find_enable_patch_row_index` logic exactly |
| **Multi-profile parallel appends** | Single lock serializes | DB must serialize append transactions globally or per-registry |
| **Eligibility change** | Forbidden in this discovery | Do not optimize recalc in same project |

---

## 9. Required tests (for implementation Task)

### 9.1 Characterization (keep green)

Existing suites to preserve behavior:

- `tests/unit/test_wallet_editor_dropbox_registry.py`
- `tests/unit/test_wallet_editor_registry_outbox.py`
- `tests/unit/test_wallet_editor_registry_timeout.py`
- `tests/unit/test_wallet_editor_registry_enable_patch.py`
- `tests/unit/test_wallet_editor_registry_refresh.py`
- `tests/unit/test_wallet_editor_auto_enable_eligibility*.py`
- `tests/unit/test_wallet_editor_auto_enable_orchestrator*.py`
- `tests/unit/test_wallet_editor_hold_enforcement.py`

### 9.2 New tests for migration

| Area | Tests |
|------|-------|
| **RegistryStore contract** | Same inputs → same `recalculate_all_results` output for Excel vs Postgres backends |
| **Append idempotency** | Double append same `run_id` → no duplicate fingerprints |
| **Repair** | processed run_id without rows → replay inserts missing fingerprints |
| **Patch** | auto-enable outcomes patch correct row; no match → warning unchanged |
| **Concurrency** | Two parallel appends → no lost rows (integration with Postgres) |
| **Import** | Golden xlsx → import → export matches fingerprints (not necessarily formatting) |
| **Reconciliation** | Inject drift → job reports |
| **Rollback flag** | `dropbox` flag uses old path; no import side effects |
| **hold/otlezka** | Enforcement identical when data synced |

### 9.3 Manual / staging

- Full disable run → outbox → replay on clean DB
- `/registry_health` after forced failure
- `/auto_enable_plan` + `/auto_enable_run` on DB-backed registry
- Operator edits `Отлёжка` in Excel → sync → lifecycle refresh changes `Дата включения`

---

## 10. Minimal implementation task (next step)

**Suggested Task ID:** `TASK-2026-06-23-01_wallet_editor_registry_postgres_phase1`

| Field | Value |
|-------|-------|
| **Goal** | Phase 1: Postgres schema + one-time import + shadow reconcile (no read path switch) |
| **Scope** | New module `integrations/wallet_editor_registry_store/`; migration SQL; import CLI; reconciliation job; feature flag `WALLET_EDITOR_REGISTRY_SHADOW_SYNC=0` |
| **Out of scope** | Changing append/patch read path; hold/otlezka edit UI; removing Dropbox |
| **Affected pipelines** | P-WE |
| **Impact** | **Medium** — new infra dependency, background jobs, no user-visible behavior change when flag off |
| **Success criteria** | Import prod xlsx snapshot → DB; shadow sync after each successful Dropbox append; daily reconcile report TG route; all existing WE registry tests green |
| **Constraints** | Preserve outbox semantics; no eligibility changes; flag off = zero Postgres calls in hot path |

**Prerequisites before Task `ready`:**

1. Railway Postgres service provisioned (user action)
2. Architect confirms hold/otlezka sync strategy for Phase 1 (Excel remains SoT for those sheets)
3. Impact Analysis per `workflow.md` §6 (pipeline P-WE + contracts)

---

## Appendix A — Environment / contracts (new, draft)

| Variable | Phase | Purpose |
|----------|-------|---------|
| `DATABASE_URL` | 1+ | Railway Postgres connection |
| `WALLET_EDITOR_REGISTRY_SHADOW_SYNC` | 1 | `1` = dual-write after Dropbox success |
| `WALLET_EDITOR_REGISTRY_SOURCE` | 2 | `postgres` \| `dropbox` |
| `WALLET_EDITOR_REGISTRY_EXCEL_EXPORT` | 3 | `1` = enable export job |

Add to `contracts.md` only after Task merge (§9 KB rules).

---

## Appendix B — Files inventory

| File | Lines of interest |
|------|-------------------|
| `integrations/wallet_editor_registry.py` | append, patch, health, replay |
| `integrations/wallet_editor_registry_xlsx.py` | Excel I/O |
| `integrations/wallet_editor_registry_lifecycle.py` | columns, recalc, fingerprints |
| `integrations/wallet_editor_registry_async.py` | outbox |
| `integrations/wallet_editor_registry_refresh.py` | daily lifecycle |
| `integrations/wallet_editor_registry_settings.py` | timeouts |
| `integrations/wallet_editor_hold.py` | hold read |
| `integrations/wallet_editor_auto_enable.py` | plan + patch orchestration |
| `integrations/wallet_editor_auto_enable_eligibility.py` | candidate selection |
| `integrations/wallet_editor_auto_enable_executor.py` | Antares + hold check |
| `automation/worker.py` | `prepare_registry_outbox_and_schedule` (disable only) |
| `integrations/tg_commands.py` | registry commands + jobs |
| `project_memory/contracts.md` | § Wallet Editor Dropbox registry |
| `project_memory/decisions.md` | E-WE-07…E-WE-20 |
