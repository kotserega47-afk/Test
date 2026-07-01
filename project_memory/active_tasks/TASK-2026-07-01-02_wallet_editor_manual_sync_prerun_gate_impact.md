# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-07-01-02 |
| **Связанная задача** | TASK-2026-07-01-02 |
| **KB версия** | v1.8 |
| **Триггер** | P-WE hot-path wiring; fail-closed gate; TG operator UX; medium risk |

---

## Current Runtime Behavior

**Entry:** `scheduler.py` → WalletEditor TG handler → per-profile worker queue → Playwright engines.

**Phase 1 delivered:** `ensure_manual_snapshot_current()`, PG tables, health library — **inactive** at runtime (`WALLET_EDITOR_MANUAL_SYNC_ENABLED=0`).

**Dangerous ops today:** start immediately after TG ingest / command; hold/Отлёжка read from Dropbox on demand inside engine/planning/refresh — **no** pre-sync gate, **no** per-run snapshot binding.

---

## Runtime Paths

| Путь | Entry | Gate today | Phase 2 |
|------|-------|------------|---------|
| Manual disable | `wallet_editor_tg` → `add_task` | нет | **да** (flag on) |
| add_partner (hold) | `engine.run` pre-pass | нет | **да** at ingest |
| add_wallet (create) | `add_add_wallet_task` | нет | **нет** |
| edit_wallet | `add_edit_wallet_task` | нет | **нет** |
| `/auto_enable_plan` | `run_auto_enable_plan` | нет | **да** |
| `/auto_enable_run` | `run_auto_enable_execute` | нет | **да** |
| `/registry_replay` | `run_registry_outbox_replay_job` | нет | **да** |
| `wallet_editor_registry_refresh` job | scheduler / TG | нет | **да** |
| `/registry_health` | `build_registry_health_report` | нет manual block | **additive block** |

**Failure path (Phase 2, flag on):** sync structural error → TG message → **return before queue/engine** → Playwright never started.

---

## Contracts Impact

| Контракт | Тип | Breaking | Потребители |
|----------|-----|----------|-------------|
| `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | behavior when `1` | **yes** for gated ops | all P-WE dangerous paths |
| Gated TG UX | new fail-closed message | additive | operators |
| `/registry_health` | new section | additive | ops |
| `hold`/`Отлёжка` SoT in readers | **unchanged** | нет | hold, lifecycle, auto-enable |
| `RunSnapshotBinding` on tasks | new optional fields | нет (additive) | worker, logging |

**Important:** Phase 2 introduces **operational** dependency on PG + Dropbox metadata for gated starts, but **does not** change which sheet is read during enforcement (still Dropbox until Phase 3).

STALE_RISK: **S4** not affected.

---

## Pipeline Impact

| P# | Happy path (flag on) | Failure path | Latency |
|----|----------------------|--------------|---------|
| **P-WE disable** | sync → binding → queue → engine | sync fail → TG block | +rev check; +download on rev change |
| **P-WE auto-enable** | sync → plan/execute | sync fail → TG block | same |
| **Registry refresh** | sync → recalc | sync fail → job abort + alert | same |
| **Registry replay** | sync → replay | sync fail → command error | same |

**Flag off:** identical to today (logged bypass).

---

## Integration Impact

| Интеграция | Phase 2 |
|------------|---------|
| **Dropbox** | Extra `get_dropbox_file_rev` per gated start; conditional download via existing manual sync |
| **PostgreSQL** | Read/write on gated starts (sync); `DATABASE_URL` required when flag on |
| **Telegram** | New failure template; extended `/registry_health` |
| **Antares** | **Unchanged** — only reached after gate OK |

---

## Cache / State Impact

| Артефакт | Change |
|----------|--------|
| `we_registry_meta` | Updated on each gated sync (Phase 1 keys) |
| Task binding | In-memory per run — `RunSnapshotBinding` on task/job |
| STATE_DIR outbox | Optional log fields; no required schema change |
| In-process hold cache | **Unchanged** — still Dropbox reader |

---

## Background / Scheduler Impact

| Job | Phase 2 |
|-----|---------|
| `wallet_editor_registry_refresh` | Gate at job entry; failure → skip refresh + log/alert |
| `wallet_editor_registry_replay` | Gate before replay mutations |
| Other scheduler jobs | **Unchanged** |

---

## Data Impact

| Данные | Phase 2 |
|--------|---------|
| PG `we_registry_hold` / `we_registry_otlezka` | Updated on gated sync when hash/rev changes |
| Dropbox `hold`/`Отлёжка` | Still operator input; gate syncs to PG |
| Runtime enforcement reads | **Still Dropbox** — PG is synced copy, not yet SoT for readers |

**Drift window (R-P2-02):** between successful PG sync and Dropbox read inside same run, theoretically consistent if no mid-run Excel edit; binding prevents re-sync but readers still hit Dropbox — acceptable until Phase 3.

---

## Regression Risks

| # | Риск | Вероятность | Обнаружение |
|---|------|-------------|-------------|
| 1 | Gate blocks all WE ops during Dropbox outage | med | integration tests; ops bypass flag |
| 2 | Forgotten gate on one dangerous path | med | checklist in Task § Gated operations; test matrix |
| 3 | Double sync per run (TG + worker) | low | single helper; test 11 |
| 4 | Operator confusion: gate OK but hold still from Dropbox | med | document § Operations still reading Dropbox |
| 5 | `/registry_health` regression | low | test 10; preserve existing blocks |
| 6 | Auto-enable partial start without binding | med | gate at plan+execute entry |
| 7 | `ENABLED=1` without DDL | low | ops checklist from Phase 1 |

---

## Rollback Strategy

| Уровень | Действие |
|---------|----------|
| Config | `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` — immediate legacy behavior |
| Code | revert deploy |
| Data | PG hold/otlezka tables harmless; meta keys optional |
| TG | failure messages disappear with flag off |

---

## Вердикт impact

| Поле | Значение |
|------|----------|
| **Уровень риска** | **medium** (hot path, fail-closed) |
| **Рекомендация** | **proceed with caution** |

**Условия proceed:**

1. Phase 1 merged and schema v2 applied in target env.
2. Default flag remains `0` until staging validation.
3. Explicit deliverable lists Dropbox readers unchanged (Phase 3 follow-up).
4. D8 emergency bypass documented for ops.
5. All 12 required tests green before `ENABLED=1` in prod.

**Benefits:** operator-visible sync health; fail-closed on bad Excel; snapshot binding foundation for Phase 3.

**Costs:** added latency on rev change; temporary dual-path (PG sync + Dropbox read).

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Impact created for TASK-2026-07-01-02 |
