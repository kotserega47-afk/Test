# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-07-01-03 |
| **Связанная задача** | TASK-2026-07-01-03 |
| **KB версия** | v1.8 |
| **Триггер** | P-WE reader SoT change; dual-flag rollout; eliminates gate/PG vs Dropbox drift |

---

## Current Runtime Behavior

**Phase 1+2 complete:** manual sync library, schema v2, pre-run gate at execution start, `RunSnapshotBinding`, `/registry_health` manual block.

**Reader SoT today:** all runtime consumers of `hold`/`Отлёжка` download from `DROPBOX_WALLET_EDITOR_PATH` (full or partial workbook), even when `WALLET_EDITOR_REGISTRY_SOURCE=postgres` for history.

**Drift risk:** gate syncs PG → enforcement reads Dropbox → inconsistent hold/otlezka within same run.

---

## Runtime Paths

| Путь | Readers today | Phase 3 (`MANUAL_READERS_SOURCE=postgres`) |
|------|---------------|---------------------------------------------|
| `add_partner` hold check | Dropbox | **PG** `we_registry_hold` |
| Auto-enable executor hold | Dropbox | **PG** |
| Auto-enable planning | Dropbox full workbook | PG history + PG manual tables |
| Lifecycle refresh | Dropbox hold/otlezka | **PG** |
| `/registry_health` lifecycle | Dropbox hold/otlezka | **PG** |
| Manual sync | Dropbox manual sheets | **unchanged** (only ingest) |
| `edit_wallet` | n/a | **unchanged** |

---

## Contracts Impact

| Контракт | Change | Breaking |
|----------|--------|----------|
| `hold`/`Отлёжка` runtime SoT | Dropbox → PG (when flag on) | **behavioral** when flag enabled |
| **new** `WALLET_EDITOR_MANUAL_READERS_SOURCE` | additive | no (default `dropbox`) |
| `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | must be on for safe postgres cutover | ops process |
| `contracts.md` hold enforcement | semantic: PG-backed after cutover | program yes; phased |
| Missing partner Отлёжка | warn not block | **unchanged** |

STALE_RISK: **S4** not affected.

---

## Pipeline Impact

| P# | Happy path (postgres readers + sync on) | Failure |
|----|----------------------------------------|---------|
| **P-WE disable** | gate → PG hold check matches synced data | PG empty + gate off → fail-closed hold |
| **P-WE auto-enable** | planning from PG; no workbook download | clear error if PG unavailable |
| **Registry refresh** | lifecycle from PG hold/otlezka | same |

**Latency:** removes full/partial Dropbox download from hot paths when postgres readers enabled — **improvement** on rev-unchanged runs (gate may still check rev).

---

## Integration Impact

| Интеграция | Phase 3 |
|------------|---------|
| **PostgreSQL** | Read-heavy on `we_registry_hold`, `we_registry_otlezka`; requires `DATABASE_URL` when readers=postgres |
| **Dropbox** | Manual ingest only (sync) + legacy fallback + export/append (unchanged) |
| **Telegram** | Health text change only |
| **Antares** | Unchanged |

---

## Cache / State Impact

| Артефакт | Change |
|----------|--------|
| PG hold/otlezka tables | Read on every gated operation (postgres mode) |
| In-process hold cache | **None today** — consider avoiding new cache (read PG per run; consistent with binding) |
| `RunSnapshotBinding` | Readers should not re-sync; optional assert/log binding hash vs PG meta in tests |

---

## Data Impact

| Данные | Phase 3 |
|--------|---------|
| `we_registry_hold` / `we_registry_otlezka` | **Runtime SoT** when readers=postgres |
| Dropbox manual sheets | Input only via ManualSync |
| Legacy `all_results`/`runs` in Dropbox | Still ignored by readers; stale |

---

## Regression Risks

| # | Risk | Prob | Mitigation |
|---|------|------|------------|
| 1 | `postgres` readers without `sync` enabled | med | health warning; fail-closed hold; ops runbook |
| 2 | PG/DataFrame column mismatch vs Excel | med | reuse `HOLD_COLUMNS`/`OTLEZKA_COLUMNS`; golden tests |
| 3 | Fallback flag left on in prod | med | health shows source; staged rollout |
| 4 | Empty PG after first deploy | med | bootstrap sync before readers=postgres |
| 5 | refresh/postgres_source dual paths diverge | med | single `load_hold_otlezka_frames_from_postgres` |
| 6 | auto-enable tests assume Dropbox download | med | update mocks in orchestrator tests |

---

## Rollback Strategy

| Level | Action |
|-------|--------|
| Config | `WALLET_EDITOR_MANUAL_READERS_SOURCE=dropbox` — immediate legacy readers |
| Config | `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` — disable gate (legacy end-to-end) |
| Code | revert deploy |
| Data | PG tables retain last-good; unused when dropbox readers |

---

## Flag default recommendation

| Flag | Recommended default | Rationale |
|------|---------------------|-----------|
| `WALLET_EDITOR_MANUAL_READERS_SOURCE` | **`dropbox`** | Zero behavior change on deploy; explicit ops flip after gate smoke |
| Pairing for prod cutover | `SYNC_ENABLED=1` + `READERS_SOURCE=postgres` | Closes gate/PG/readers loop |

Alternative rejected: default `postgres` — too aggressive without guaranteed initial sync on all envs.

---

## Вердикт impact

| Поле | Значение |
|------|----------|
| **Уровень риска** | **medium** |
| **Рекомендация** | **proceed with caution** |

**Условия proceed:**

1. Phase 2 merged and gate smoke-tested.
2. Default readers flag `dropbox` until staging validation.
3. Documented fail-closed behavior for gate-off + postgres readers (test #9).
4. Health must expose readers source and fallback warning.
5. No export/Dropbox write changes in this Task.

**Benefits:** eliminates gate/PG vs Dropbox drift; fewer downloads; aligns with E-WE-21 history-in-PG direction.

**Costs:** `DATABASE_URL` hard dependency in postgres readers mode; ops must coordinate two flags.

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Impact created for TASK-2026-07-01-03 |
