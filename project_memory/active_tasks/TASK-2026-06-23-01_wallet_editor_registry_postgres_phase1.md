# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-06-23-01 |
| **Статус** | draft |
| **Приоритет** | medium |
| **KB версия** | v1.7 |
| **Связанные артефакты** | `TASK-2026-06-23-01_wallet_editor_registry_postgres_phase1_impact.md`, `CP-dev_task-TASK-2026-06-23-01-20260623.md`, `DISCOVERY-2026-06-23_wallet_editor_registry_postgres_migration.md` |
| **Impact** | required — см. `_impact.md` |
| **Context Pack** | required — `CP-dev_task-TASK-2026-06-23-01-20260623.md` |
| **Prerequisite** | Railway PostgreSQL provisioned + `DATABASE_URL` on Railway |

---

## Goal

Подготовить безопасную миграцию WalletEditor Registry с Excel на PostgreSQL **без изменения текущего runtime-поведения**.

Phase 1 не меняет source of truth: Excel (`wallet_editor.xlsx` в Dropbox) остаётся единственным источником данных. PostgreSQL используется только как mirror для накопления данных и проверки будущей миграции (shadow-write после успешной записи в Excel).

---

## Business Context

WalletEditor Registry фактически выполняет роль БД: история операций, lifecycle, auto-enable eligibility, replay, health, repair. При этом SoT — Excel в Dropbox. Уже есть durable outbox, replay, health — признак того, что Excel вышел за пределы первоначальной роли.

Цель — контролируемый переход к PostgreSQL без изменения пользовательского поведения. Потребитель: ops (registry health), будущий Phase 2 (DB SoT). Связь: **P-WE**, **R6** (`current_state.md`).

---

## Current Behavior

**Source of truth:** Dropbox `{DROPBOX_WALLET_EDITOR_PATH}` → `wallet_editor.xlsx`.

**Write path:** `download_file_with_rev` → mutate (openpyxl) → `upload_file_if_rev` + in-process `threading.Lock` + outbox/replay.

**Sheets:**

| Sheet | Использование |
|-------|---------------|
| `all_results` | основная история (append, patch, lifecycle recalc) |
| `runs` | история запусков disable |
| `hold` | ручные блокировки (operator-owned, code read-only) |
| `Отлёжка` | lifecycle days (operator-owned, code read-only) |

**Registry consumers:**

| Flow | Registry usage |
|------|----------------|
| Manual disable | append (via outbox) |
| Conversion → WalletEditor | append |
| Auto-enable plan | read |
| Auto-enable execute (B2) | patch `Включено` / `Комментарий включения` |
| Registry refresh | recalc lifecycle + conditional upload |
| Registry health / replay | read/write outbox + read registry |
| Hold enforcement | read `hold` |

**Not using registry:** add_wallet, edit_wallet (E-WE-19).

KB: `contracts.md` § Wallet Editor Dropbox registry; `decisions.md` E-WE-07…E-WE-20.

---

## Desired Behavior

После **полного успешного Excel commit** — upload completed (append / patch / lifecycle refresh):

```
Excel upload SUCCESS → PostgreSQL mirror write (best-effort) → log on failure
```

**Запрещено (I-REG-08):** `Excel FAIL` + `Postgres SUCCESS` — mirror не запускается, если Excel upload не завершён успешно.

- Все runtime read paths продолжают читать **только Excel**.
- PostgreSQL не участвует в принятии решений (auto-enable, hold, lifecycle).
- При `WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED=0` — zero Postgres calls в hot path.
- `/registry_health` дополнительно показывает mirror status (pending drift, last reconcile).

---

## Affected Modules

| Модуль / файл | Статус в KB | Действие |
|---------------|-------------|----------|
| `integrations/wallet_editor_registry.py` | CONFIRMED active | hook shadow-write после SUCCESS append/patch |
| `integrations/wallet_editor_registry_refresh.py` | CONFIRMED active | hook shadow-write после SUCCESS lifecycle upload |
| `integrations/wallet_editor_registry_async.py` | CONFIRMED active | не менять outbox semantics |
| `integrations/wallet_editor_registry_lifecycle.py` | CONFIRMED active | не менять; reuse fingerprint helpers |
| `integrations/wallet_editor_auto_enable*.py` | CONFIRMED active | **не трогать** read path |
| `integrations/wallet_editor_hold.py` | CONFIRMED active | **не трогать** |
| `automation/worker.py` | CONFIRMED active | **не трогать** (indirect via registry) |
| `integrations/tg_commands.py` | CONFIRMED active | extend `/registry_health` output |
| **new** `integrations/wallet_editor_registry_db/` | new | schema, mirror, import, reconcile |
| **new** `tools/import_wallet_editor_registry.py` (or similar) | DEV_ONLY | initial import CLI |
| `tests/unit/test_wallet_editor_registry_*.py` | CONFIRMED | extend + new db tests |
| `project_memory/contracts.md` | KB | update post-merge (env flags) |

---

## Affected Pipelines

| ID | Пайплайн | Затронут | Happy / Failure path |
|----|----------|----------|----------------------|
| **P-WE** | WalletEditor | **да** | disable append → mirror; mirror fail → log only, TG/outbox unchanged |
| P3 (conversion) | Conversion → WE bridge | косвенно | same append path |
| — | add_wallet / edit_wallet | нет | — |

**Background jobs:** `wallet_editor_registry_refresh`, `wallet_editor_registry_replay` — mirror/reconcile hooks only when flag on.

---

## Affected Contracts

| Контракт | Breaking? | Комментарий |
|----------|-----------|---------------|
| Dropbox registry xlsx (`contracts.md`) | **нет** | SoT unchanged |
| Outbox (`STATE_DIR/wallet_editor/outbox`) | **нет** | I-REG-04 |
| `registry_processed_run_ids.json` | **нет** | I-REG-05 |
| `DROPBOX_WALLET_EDITOR_PATH` | **нет** | unchanged |
| **new** `DATABASE_URL` | additive | optional until flag on |
| **new** `WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED` | additive | default `0` |

STALE_RISK: **S4** (dual validation) — unrelated; **R-WE-07** (concurrent registry writes) — mirror must be idempotent.

---

## Constraints

- **I-REG-01…08** (см. § Critical Invariants ниже) — обязательны.
- Не активировать DORMANT (`analyzers/transactions.py`).
- Не менять `recalculate_all_results()` semantics.
- Не менять auto-enable eligibility logic.
- Не переключать read path на Postgres.
- Не мигрировать `hold` / `Отлёжка` в Phase 1.
- Postgres failure **must not** raise into append/patch/replay success path (I-REG-03).
- Secrets: только `DATABASE_URL` env name in docs; no credentials in Task/Pack.
- Minimal diff: new module + thin hooks in existing write success paths.

---

## Critical Invariants (task-local)

| ID | Инвариант |
|----|-----------|
| **I-REG-01** | Excel остаётся единственным source of truth |
| **I-REG-02** | Все read paths читают Excel |
| **I-REG-03** | Ошибка PostgreSQL не ломает WalletEditor runtime |
| **I-REG-04** | Outbox semantics не меняются |
| **I-REG-05** | Replay semantics не меняются |
| **I-REG-06** | Auto-enable eligibility полностью Excel-based |
| **I-REG-07** | Lifecycle logic не изменяется |
| **I-REG-08** | Mirror write **только** после полного успешного Excel commit (`upload_file_if_rev` → `uploaded`); состояние `Excel FAIL` + `Postgres SUCCESS` **недопустимо** |

Связанные KB: **E-WE-07…10, E-WE-20** (registry durability); **I3** (no DORMANT without E#).

**I-REG-08 hook placement:** вызов mirror — строго после `_AppendOutcome.SUCCESS` / `_PatchOutcome.SUCCESS` / refresh `_RefreshOutcome.SUCCESS` (т.е. после `mark_run_processed` / подтверждённого upload), **не** до `upload_file_if_rev`, **не** на `DUPLICATE` без проверки что Excel актуален (duplicate = skip mirror re-insert или idempotent no-op only).

---

## Feature Flags

| Env | Default | Behavior |
|-----|---------|----------|
| `WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED` | `0` | `0` = Postgres fully off; `1` = shadow-write after Excel success |

Optional (implementation detail, document in contracts post-merge):

- `DATABASE_URL` — required when mirror enabled

---

## Known Risks

| Риск | Источник | Вероятность | Митигация |
|------|----------|-------------|-----------|
| R1 Registry drift Excel↔DB | new | med | reconcile job + health |
| R2 Duplicate mirror rows on replay | R-WE-07 pattern | med | UNIQUE on `row_fingerprint`; idempotent upsert |
| R3 Hidden lifecycle coupling | discovery | low | lifecycle untouched in Phase 1 |
| R4 PostgreSQL outage | new | med | best-effort mirror; Excel primary |
| R5 Hold/Отлёжка drift | operator Excel edits | med | **out of scope** Phase 1; reconcile only `all_results`+`runs` |
| Concurrent append | `wallet_editor_registry._lock` | low | mirror in same success path after Excel upload |

---

## Investigation Status (pre-implementation)

| # | Item | Status |
|---|------|--------|
| 1 | All write paths | **CONFIRMED** — discovery §2 |
| 2 | All read paths | **CONFIRMED** — discovery §2 |
| 3 | Auto-enable dependencies | **CONFIRMED** — read Excel only; no change |
| 4 | Lifecycle dependencies | **CONFIRMED** — `recalculate_all_results` unchanged |
| 5 | Idempotent mirror write | **FEASIBLE** — `result_row_fingerprint` exists |
| 6 | Reconcile by keys | **FEASIBLE** — fingerprint + run metadata |

Ref: `DISCOVERY-2026-06-23_wallet_editor_registry_postgres_migration.md`.

---

## Proposed PostgreSQL Schema (Phase 1 subset)

> `hold` / `Отлёжка` **не** в Phase 1 mirror tables.

### `we_registry_results`

Содержимое `all_results` (post-recalc rows at mirror time).

| Column | Notes |
|--------|-------|
| `id` | BIGSERIAL PK |
| `run_id` | UUID/text, nullable for legacy rows |
| `operation_date` | `Дата операции` |
| `disable_at` | `Дата отключения` |
| `reenable_date` | `Дата включения` |
| `enable_status` | `Статус включения` |
| `vklyucheno` | `Включено` |
| `enable_comment` | `Комментарий включения` |
| `card`, `partner`, `action`, `status`, `comment`, `hold_mark` | |
| `row_fingerprint` | UNIQUE — same algo as `lifecycle.result_row_fingerprint` |
| `source_row_index` | for traceability |
| `created_at`, `updated_at` | |

### `we_registry_runs`

Содержимое `runs` + metadata for traceability.

| Column | Notes |
|--------|-------|
| `run_id` | PK (generated UUID for new runs; import may synthesize) |
| `started_at`, `finished_at` | |
| `input_rows`, `success_rows`, `failed_rows`, `skipped_rows` | |
| `output_file` | |
| `operator_profile`, `source` | optional, from outbox when available |
| `created_at` | |

### `we_registry_reconcile`

Drift audit log (not SoT).

| Column | Notes |
|--------|-------|
| `id` | BIGSERIAL PK |
| `detected_at` | |
| `entity_type` | `results` \| `runs` \| `aggregate` |
| `entity_key` | fingerprint or run_id |
| `excel_hash` | optional content hash |
| `db_hash` | optional |
| `status` | `open` \| `resolved` \| `ignored` |
| `details` | JSON/text |

---

## Rollout Strategy

| Stage | Action | Mirror flag |
|-------|--------|-------------|
| **A** | Deploy schema migration; flag `0` | off |
| **B** | One-time import Excel → Postgres | off |
| **C** | Enable shadow-write (`=1`) | on |
| **D** | Observation ≥7 days; reconcile stats | on |

---

## Verification Strategy

**Unit:** schema mapping, mirror upsert idempotency, duplicate protection, flag-off no-op, DB failure swallowed, **I-REG-08:** mirror not called on Excel rev_conflict/timeout/transient before success.

**Invariant test (I-REG-08):** simulate Excel upload failure → assert mirror writer **never** invoked; simulate Excel SUCCESS → mirror may run.

**Integration:** append→mirror, patch→mirror, refresh upload→mirror, reconcile mismatch detection.

**Manual:** disable, conversion→WE, auto-enable patch, refresh, replay, health — behavior unchanged vs pre-Phase-1.

**Regression:** all existing `test_wallet_editor_registry_*`, `test_wallet_editor_dropbox_registry`, auto-enable suites green.

---

## Success Criteria

- [ ] PostgreSQL schema создана (migration SQL)
- [ ] Initial import utility реализован
- [ ] Shadow-write после Excel SUCCESS (append, patch, refresh upload)
- [ ] Read path не изменён
- [ ] Auto-enable работает без изменений
- [ ] Replay работает без изменений
- [ ] `/registry_health` показывает mirror status
- [ ] Reconcile job/CLI реализован
- [ ] `WALLET_EDITOR_REGISTRY_DB_MIRROR_ENABLED=0` — полностью старое поведение
- [ ] **I-REG-08:** тесты подтверждают — mirror не вызывается при Excel upload fail; Excel FAIL + Postgres SUCCESS невозможен
- [ ] Observation period ≥7d без критических drift (ops sign-off)

---

## Out Of Scope

- Переключение source of truth (Phase 2)
- PostgreSQL read path
- Auto-enable / lifecycle из Postgres
- Замена Excel для операторов
- Export pipeline (Phase 3)
- Hold / Отлёжка migration
- Registry redesign
- Замена outbox на DB (optional future)

---

## Deliverables

| Type | Artifact |
|------|----------|
| Code | `integrations/wallet_editor_registry_db/` — schema, mirror, reconcile |
| Code | import CLI tool |
| Code | hooks in registry write success paths |
| Code | health extension |
| Tests | unit + integration per Verification Strategy |
| Docs | Impact (done), CP (done), Review (before merge) |
| KB | `contracts.md` env flags — post-merge |

---

## Next Task

**TASK-2026-XX-XX-02** — WalletEditor Registry PostgreSQL Source Of Truth (Phase 2).  
Допускается только после observation без критических расхождений Excel↔Postgres.

---

## Workflow decisions

| Decision | Value |
|----------|-------|
| Impact required? | **yes** — P-WE, new env, persistence |
| Context Pack required? | **yes** — `dev_task` |
| Transition `draft` → `ready` | After Railway Postgres provisioned + Impact `proceed` |

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-06-23 | Initiator + Architect | Task draft from discovery + user spec |
