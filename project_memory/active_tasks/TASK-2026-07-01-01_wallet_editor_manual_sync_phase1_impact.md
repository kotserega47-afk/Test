# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-07-01-01 |
| **Связанная задача** | TASK-2026-07-01-01 |
| **KB версия** | v1.8 |
| **Триггер** | P-WE, new PG tables, workbook contract repurposing, medium risk; program affects contracts |

---

## Current Runtime Behavior

**Entry:** `railway.toml` → `scheduler.py` (E1, E2).

**WalletEditor (S8, R6):** ACTIVE — Telegram Excel → worker → Antares; registry history in PostgreSQL when `WALLET_EDITOR_REGISTRY_SOURCE=postgres` (E-WE-21).

**Operator sheets:** `hold` + `Отлёжка` read from Dropbox `wallet_editor.xlsx` at `DROPBOX_WALLET_EDITOR_PATH` (combined workbook today: also `all_results`, `runs`).

**Export:** `/registry_export` uploads rebuilt workbook to Dropbox (`excel_export.py`).

**No manual sync gate** exists today. **No new Dropbox path** — program reuses `DROPBOX_WALLET_EDITOR_PATH` with repurposed contract (manual-input only at steady state).

---

## Runtime Paths

| Путь | Entry | Модули | Phase 1 изменяется? |
|------|-------|--------|----------------------|
| Manual disable | TG → worker | registry append | **нет** (flag off) |
| add_partner / add_wallet | TG → worker | hold check via Dropbox | **нет** |
| Auto-enable | `/auto_enable_*` | Dropbox registry read | **нет** |
| Registry replay / refresh | TG / scheduler | registry + lifecycle | **нет** |
| Registry export | `/registry_export` | excel_export → Dropbox | **нет** |
| **New (library only)** | unit tests / future gate | `manual_sync.*` | **new code, inactive** |

---

## Contracts Impact

| Контракт | Тип изменения | Breaking | Потребители |
|----------|---------------|----------|-------------|
| `DROPBOX_WALLET_EDITOR_PATH` | **path unchanged**; **semantic repurposing (program)** | **program yes** for readers of `all_results`/`runs` in Dropbox | registry, export, hold, auto-enable |
| `wallet_editor.xlsx` sheets | program: manual-only target (`hold`, `Отлёжка`) | phased | ops, runtime |
| Legacy `all_results` / `runs` in Dropbox | ignored by manual sync; **not deleted Phase 1** | нет Phase 1 | manual_sync only |
| `DATABASE_URL` | schema extension | нет | new tables |
| Hold / Отлёжка column defs | semantic unchanged | нет | lifecycle, hold |
| Snapshot hash `v1` | new contract | нет | manual_sync only |

**Not required Phase 1:** new Dropbox path/env (`DROPBOX_WALLET_EDITOR_MANUAL_PATH` deferred per D7).

STALE_RISK: **S4** not affected.

---

## Pipeline Impact

| P# | Happy path | Failure path | Phase 1 |
|----|------------|--------------|---------|
| **P-WE** | unchanged | unchanged | **zero** observable change (flag `0`) |
| P3 → WE | unchanged | unchanged | **нет** |

**Post Phase 2+ (program):** pre-run Dropbox metadata call + conditional download before dangerous ops; adds latency on rev change only.

---

## Integration Impact

| Интеграция | Сейчас | Phase 1 | Program (later) |
|------------|--------|---------|-----------------|
| **Dropbox** | combined workbook at `DROPBOX_WALLET_EDITOR_PATH` | **no new path**; manual sync library reads same path, **hold/Отлёжка only** | workbook repurposed; export **stops** uploading; legacy sheets removed only after ops Task |
| **PostgreSQL** | history tables | **+3 tables** DDL | hold/otlezka SoT after sync |
| **Telegram** | results, commands | unchanged | export file delivery |
| **Antares** | Playwright | **нет** | **нет** |

**Rollout complexity:** **lower** — no second Dropbox file or env for operators during migration.

---

## Cache Impact

| Артефакт | Путь | Phase 1 |
|----------|------|---------|
| `we_registry_meta` | PostgreSQL | new keys for manual sync |
| In-process mirror/export state | module globals | **not used** for manual sync (durable PG meta) |
| STATE_DIR outbox | `/data/state/wallet_editor/` | unchanged |

**Program:** per-run `manual_snapshot_hash` stored on task/outbox record (Phase 2) — no shared in-memory cache across workers.

---

## Background / Scheduler Impact

| Элемент | Phase 1 | Program |
|---------|---------|---------|
| `wallet_editor_registry_refresh` | unchanged | +pre-sync gate |
| `wallet_editor_registry_replay` | unchanged | +pre-sync gate |
| Scheduler error policy | unchanged | sync fail → block job, log + TG |

---

## Data Impact

| Данные | SoT today | Phase 1 | Program target |
|--------|-----------|---------|----------------|
| `hold` rows | Dropbox xlsx (same path) | PG tables **created**, unused | PG after sync |
| `Отлёжка` rows | Dropbox xlsx (same path) | same | PG after sync |
| `all_results` / `runs` | PostgreSQL (+ legacy copies in Dropbox) | PG unchanged; **legacy Dropbox sheets stale/ignored** by manual sync | PostgreSQL only |
| Manual sync audit | — | `we_registry_manual_sync_runs` | audit trail |

**Migration safety:** until **TASK-2026-07-01-06** (proposed cleanup Task), Dropbox workbook may retain legacy `all_results`/`runs`. Manual sync must not read or hash them. **No automatic sheet deletion in Phase 1.**

**Pre-cleanup ops Task must include:** (1) PG history completeness verification, (2) Dropbox workbook backup, (3) operator approval, (4) cleanup to `hold`+`Отлёжка` only, (5) rollback instructions.

**DDL:** ops apply `schema.sql` additions; import hold/otlezka from existing workbook sheets (Phase 2).

---

## Regression Risks

| # | Риск | Область | Вероятность | Обнаружение |
|---|------|---------|-------------|-------------|
| 1 | DDL apply failure on prod | PostgreSQL | low | migration smoke |
| 2 | Accidental flag `ENABLED=1` without wiring | P-WE | low | default `0` + tests |
| 3 | Hash algorithm change breaks idempotency | manual sync | med | version field `v` in payload |
| 4 | Duplicate detection too strict vs operator practice | validation | med | ops feedback |
| 5 | Phase 2 gate blocks all WE ops on Dropbox outage | P-WE | med | documented fail-closed; bypass flag for emergency |
| 6 | **Legacy stale sheets** (`all_results`/`runs`) mistaken for live SoT | migration | **med** | manual sync ignores; readers migrate to PG; cleanup Task |
| 7 | **Cleanup without backup** deletes operator-visible history in Excel | ops | high | separate Task; mandatory backup + PG verify (I-MAN-09) |
| 8 | Repurposed workbook contract breaks undocumented Dropbox consumers | contracts | low | KB update post-cutover; export TG-only |

---

## Rollback Strategy

| Уровень | Действие | Условие |
|---------|----------|---------|
| Config | `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` | any sync issue after Phase 2 |
| Code | revert commit / redeploy | logic bug |
| Schema | **no DROP** in Phase 1 rollback; tables unused harmless | Phase 1 only |
| Data | PG hold/otlezka tables ignored if flag off | emergency |
| Runtime | restart `scheduler.py` | — |

Phase 1 rollback = revert code; new empty tables remain (no runtime reference).

---

## Вердикт impact

| Поле | Значение |
|------|----------|
| **Уровень риска** | **medium** (program — legacy sheets + contract repurposing); **low** for Phase 1 alone (flag off, no wiring) |
| **Рекомендация** | **proceed with caution** |

**Условия proceed:**
1. Architect approves **Отлёжка missing sheet = BLOCK** policy.
2. Architect approves **comment in hash** (confirmed by Initiator).
3. Architect approves **D12** — reuse `DROPBOX_WALLET_EDITOR_PATH`; runtime stops depending on generated sheets in workbook.
4. Phase 1 explicitly excludes production wiring — separate Task for gate.
5. Phase 1 excludes Dropbox sheet cleanup — **TASK-2026-07-01-06** with backup/verify/approval.
6. DDL applied in staging before Phase 2 cutover.

**Phase 1 benefits:** no new Dropbox path/env; lower operator rollout complexity.

**Phase 1 costs:** migration period with legacy stale sheets in same workbook — must ignore in manual sync.

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Impact created for TASK-2026-07-01-01 |
| 2026-07-01 | Initiator + Cursor | D7/D12: reuse `DROPBOX_WALLET_EDITOR_PATH`; legacy sheets; cleanup Task; no new env Phase 1 |
