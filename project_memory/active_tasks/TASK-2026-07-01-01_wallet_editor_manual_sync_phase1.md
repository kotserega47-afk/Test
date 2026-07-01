# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-07-01-01 |
| **Статус** | ready |
| **Приоритет** | medium |
| **KB версия** | v1.8 |
| **Связанные артефакты** | `TASK-2026-07-01-01_wallet_editor_manual_sync_phase1_impact.md`, `CP-dev_task-TASK-2026-07-01-01-20260701.md`, `DISCOVERY-2026-06-23_wallet_editor_registry_postgres_migration.md` |
| **Impact** | required — см. `_impact.md` |
| **Context Pack** | `CP-dev_task-TASK-2026-07-01-01-20260701.md` |
| **Depends on** | E-WE-21 (Postgres registry history); `DATABASE_URL` in prod |
| **Program** | WalletEditor Registry v2 — manual Excel → PG sync → PG-only runtime |

---

## Goal

Заложить **foundation** для snapshot-based sync operator sheets (`hold`, `Отлёжка`) из **существующего** Dropbox workbook (`DROPBOX_WALLET_EDITOR_PATH`) в PostgreSQL, с normalized snapshot hash и двухуровневым skip (Dropbox `rev` → semantic hash), чтобы последующие фазы могли:

1. обязать pre-run sync перед опасными операциями;
2. изолировать snapshot на время одного запуска;
3. перевести runtime readers на PG;
4. экспортировать registry **только в Telegram** (без Dropbox upload).

**Phase 1 (этот Task)** доставляет schema + sync/snapshot модули + unit tests. **Не** включает production wiring pre-run gate, reader migration, export redesign.

---

## Business Context

WalletEditor зависит от operator-owned данных `hold` и `Отлёжка` для hold enforcement, lifecycle (`Дата включения`), auto-enable eligibility. Сегодня они читаются из combined `wallet_editor.xlsx` в Dropbox вместе с history — это создаёт coupling, online Excel patching и drift.

Целевая модель (программа): тот же Dropbox path (`DROPBOX_WALLET_EDITOR_PATH`), но **новый контракт файла** — manual-input workbook (только `hold` + `Отлёжка`); registry history — **только PostgreSQL**; export — on-demand report в Telegram (без upload в Dropbox).

**Миграция:** до отдельной ops-задачи cleanup workbook может содержать legacy листы `all_results` / `runs` — они **игнорируются** manual sync и не участвуют в hash.

Потребители: ops (sync health, export), WalletEditor operators (disable, add_partner, auto-enable). Контур: **P-WE**, **R6**.

---

## Current Behavior

| Aspect | Сейчас (CONFIRMED) |
|--------|-------------------|
| Manual sheets SoT | Dropbox `{DROPBOX_WALLET_EDITOR_PATH}` → combined `wallet_editor.xlsx` sheets `hold`, `Отлёжка` |
| History SoT | PostgreSQL `we_registry_results` / `we_registry_runs` when `WALLET_EDITOR_REGISTRY_SOURCE=postgres` (E-WE-21) |
| Hold read | `wallet_editor_hold.load_hold_pairs_from_dropbox()` — download full registry workbook |
| Auto-enable plan | `load_registry_frames_for_planning()` — download full registry workbook |
| Lifecycle | `recalculate_all_results()` uses hold/Отлёжка DataFrames from Dropbox download |
| Export | `/registry_export` → PG → rebuild xlsx → **Dropbox upload** (`excel_export.py`) |
| Missing partner on Отлёжка | Warning `Нет даты отлёжки` / missing partners set; **не блокирует** операцию |
| Missing Отлёжка sheet | If sheet absent at download: empty DataFrame; lifecycle marks missing partners |

KB: `contracts.md` § Wallet Editor registry; `decisions.md` E-WE-07…E-WE-22.

---

## Desired Behavior

### Dropbox workbook contract (program target)

| | Before | After |
|---|--------|-------|
| **Path** | `DROPBOX_WALLET_EDITOR_PATH` | **same path** (no new env in Phase 1) |
| **Role** | combined registry workbook | **manual-input workbook only** |
| **Operator sheets** | `hold`, `Отлёжка` | `hold`, `Отлёжка` (SoT for operator input) |
| **System sheets** | `all_results`, `runs` (patched online) | **not required** in Dropbox; history in PostgreSQL only |
| **Export** | upload to Dropbox | TG `send_document` only (Phase 3) |

**Phase 1 / migration:** legacy sheets `all_results`, `runs`, export/README/sync_status sheets **may still exist** in the file — manual snapshot **reads only** `hold` + `Отлёжка`; other sheets **ignored** (no hash, no read, no mutate). **Automatic deletion of legacy sheets forbidden in Phase 1.**

### Program target (full v2 — не всё в Phase 1)

1. **Reuse** `DROPBOX_WALLET_EDITOR_PATH` as manual workbook source; runtime **stops depending** on generated sheets in this file (D12).
2. **Mandatory pre-run sync** before disable / add_partner / auto-enable / replay / lifecycle refresh:
   - `get_dropbox_file_rev` → skip download if rev unchanged;
   - else download → validate → normalized snapshot hash;
   - if hash unchanged → update rev metadata only;
   - if hash changed → sync PG (soft-delete removed rows);
   - block operation on structural validation failure.
3. **Snapshot isolation per run:** at task start, record `manual_sync_run_id` + `manual_snapshot_hash`; mid-run Excel edits apply only to next run.
4. **Отлёжка:** missing **partner** → warn, continue; structural workbook errors → block (see § Отлёжка policy).
5. **Comments in hash:** hold (`card`, `partner`, optional `comment`, optional `Дата добавления`); Отлёжка (`partner`, `Полные дни`, optional `comment`).
6. **Export:** TG `send_document` only; no Dropbox upload (follow-on task).

### Phase 1 deliverable (this Task)

Implement **library layer** (feature-flagged, not wired to worker/TG hot path):

| Component | Deliverable |
|-----------|-------------|
| `manual_snapshot.py` | Parse **only** sheets `hold`/`Отлёжка` from `DROPBOX_WALLET_EDITOR_PATH`; ignore other sheets; validate; build canonical payload, `snapshot_hash` |
| `manual_sync.py` | `ensure_manual_snapshot_current()` via `wallet_editor_dropbox_path()`; rev → download → hash → sync decision; `sync_manual_to_postgres()` |
| `manual_sync_state.py` | Read/write `we_registry_meta` hot keys + last sync run; **`build_manual_snapshot_health_report()`** (library; TG wiring Phase 2) |
| `schema.sql` | Tables: `we_registry_hold`, `we_registry_otlezka`, `we_registry_manual_sync_runs`; meta keys |
| `models.py` | Dataclasses for hold/otlezka/sync run rows |
| Unit tests | Hash stability, validation, rev/hash skip logic (mocked Dropbox/PG) |

**Default in Phase 1:** `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` — zero runtime behavior change until Phase 2 wiring.

---

## Отлёжка policy (approved)

| Condition | Policy | Rationale |
|-----------|--------|-----------|
| Partner **not** in Отлёжка sheet (sheet exists or missing) | **WARN**, operation continues | D2 — matches `missing_partners` / `Нет даты отлёжки` |
| Sheet `Отлёжка` **missing entirely** | **WARN**, not global block (D1) | Empty otlezka in snapshot; lifecycle warns per partner |
| Sheet `hold` **missing entirely** | **BLOCK** | Structural error — hold enforcement requires sheet |
| Sheet exists, **bad columns** / duplicates | **BLOCK** | Structural validation (Отлёжка only if sheet present) |
| Sheet exists, **empty** (headers only) | **OK** | Valid config |

---

## Normalized snapshot contract (Phase 1)

```json
{
  "v": 1,
  "hold": [
    {"c": "<card_norm>", "p": "<partner_norm>", "a": "<added_at_str>|null", "m": "<comment>|null"}
  ],
  "otlezka": [
    {"p": "<partner_norm>", "d": <int>, "m": "<comment>|null"}
  ]
}
```

- Sorted lists for order-independence.
- `card_norm` / `partner_norm`: `strip().casefold()` (reuse `_normalize_key`).
- Empty rows skipped; duplicate `(c,p)` or `p` → validation error.
- Hash: `sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))`.
- **Legacy sheets** (`all_results`, `runs`, README, sync_status, any other): **excluded** from payload — reorder/edits on legacy sheets do not change hash.

---

## Migration safety — workbook cleanup (out of Phase 1)

Before removing `all_results` / `runs` (or any generated sheets) from the Dropbox workbook, a **separate migration/ops Task** is required with:

1. Verification that PostgreSQL contains **complete** registry history (reconcile / row counts / spot checks).
2. **Backup** of current Dropbox workbook (download + secure store).
3. **Explicit operator approval** (Initiator sign-off).
4. Cleanup plan leaving **only** `hold` + `Отлёжка` (plus optionally ignored empty legacy sheets until manual delete).
5. **Rollback instructions** (restore backup workbook; revert env if needed).

**Phase 1 explicitly forbids** automatic or scripted deletion of generated sheets from Dropbox.

---

## Open decisions (Architect)

| ID | Decision | Status |
|----|----------|--------|
| D1 | Отлёжка sheet missing entirely → **WARN**, not global block; invalid structure **if sheet exists** → block | **approved** |
| D2 | Partner missing on Отлёжка → **WARN** | **approved** |
| D3 | Comment participates in hash | **approved** |
| D4 | `Дата добавления` in hash if column present | **approved** |
| D5 | Duplicate keys → **BLOCK** | **approved** |
| D6 | Phase 1 without production wiring | **approved** |
| D7 | Reuse `DROPBOX_WALLET_EDITOR_PATH`; no new manual path env in Phase 1 | **approved** |
| D8 | Emergency bypass `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` — explicit + logged | **approved** |
| D12 | Workbook repurposed as manual-input; legacy generated sheets ignored, not deleted Phase 1 | **approved** |

---

## Affected Modules

| Модуль / файл | Статус в KB | Действие Phase 1 |
|---------------|-------------|------------------|
| **new** `wallet_editor_registry_db/manual_snapshot.py` | new | create |
| **new** `wallet_editor_registry_db/manual_sync.py` | new | create |
| **new** `wallet_editor_registry_db/manual_sync_state.py` | new | create |
| `wallet_editor_registry_db/schema.sql` | CONFIRMED | extend (v2 tables) |
| `wallet_editor_registry_db/models.py` | CONFIRMED | extend dataclasses |
| `wallet_editor_registry_db/store.py` | CONFIRMED | extend upsert hold/otlezka (if needed) |
| `wallet_editor_registry_lifecycle.py` | CONFIRMED | **read-only reuse** (`_normalize_key`, column constants) |
| `dropbox_watcher.py` | CONFIRMED | **no change** (reuse `get_dropbox_file_rev`, `download_file_with_rev`) |
| `wallet_editor_hold.py` | CONFIRMED | **не трогать** (Phase 2) |
| `wallet_editor_auto_enable.py` | CONFIRMED | **не трогать** (Phase 2) |
| `automation/worker.py` | CONFIRMED | **не трогать** (Phase 2) |
| `tg_commands.py` | CONFIRMED | **не трогать** Phase 1 (health block library only; TG Phase 2) |
| `excel_export.py` | CONFIRMED | **не трогать** (Phase 3 export) |
| **new** `tests/unit/test_wallet_editor_manual_snapshot.py` | new | create |
| **new** `tests/unit/test_wallet_editor_manual_sync.py` | new | create |

---

## Affected Pipelines

| ID | Пайплайн | Phase 1 runtime change |
|----|----------|------------------------|
| **P-WE** | WalletEditor | **нет** (flag off) |
| P3 conversion→WE | косвенно | **нет** |

---

## Affected Contracts

| Контракт | Breaking? | Phase 1 |
|----------|-----------|---------|
| `DROPBOX_WALLET_EDITOR_PATH` | **semantic change (program)**; path unchanged | **read path for manual sheets only** in new modules; existing registry code unchanged |
| `wallet_editor.xlsx` sheet contract | **program:** manual-only target | legacy `all_results`/`runs` **ignored** by manual snapshot; not deleted |
| `DATABASE_URL` | нет | new tables require migration apply (ops) |
| **new** `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | additive | default `0` |
| Hold columns (`HOLD_COLUMNS`) | нет | same semantics |
| Отлёжка columns (`OTLEZKA_COLUMNS`) | нет | same semantics |
| Outbox / processed_run_ids | нет | unchanged |

---

## Constraints

- **I-MAN-01:** Sync validation failure must **not** mutate last good PG hold/otlezka (transaction rollback).
- **I-MAN-02:** Export failure must **never** roll back DB (program rule; Phase 3).
- **I-MAN-03:** Mid-run manual Excel changes must **not** affect in-flight run snapshot (**Phase 2** wiring).
- **I-MAN-10 — Runtime Snapshot Consistency:** For a single WalletEditor run, the manual snapshot is **fixed at run start**. Each run records `manual_sync_run_id`, `manual_snapshot_hash`, `manual_snapshot_synced_at`. If the operator edits the Dropbox workbook during the run, the current run continues with the snapshot captured at start; new manual changes apply only to subsequent runs. Applies to: disable; add_partner/add_wallet (when hold relevant); auto-enable plan/run; registry replay; lifecycle refresh. **Phase 1:** dataclass/API only; worker wiring in Phase 2.
- **I-MAN-04:** Do not activate DORMANT (`analyzers/transactions.py`).
- **I-MAN-05:** Phase 1 — **no** changes to Playwright/Antares flows.
- **I-MAN-06:** Phase 1 — **no** removal of Dropbox registry write path or `schedule_excel_export`.
- **I-MAN-07:** Reuse `get_dropbox_file_rev` for metadata-only check; do not use `workbook_sha256` for semantic compare.
- **I-MAN-08:** Manual snapshot reads **only** `hold` + `Отлёжка`; ignore all other sheets; never mutate Dropbox workbook in Phase 1.
- **I-MAN-09:** Do **not** auto-delete `all_results` / `runs` / generated sheets from Dropbox (separate ops Task with backup + approval).
- Secrets: only env **names** in Task; no credentials in artifacts.

---

## `/registry_health` — Manual snapshot block (program; library in Phase 1)

Operators must see whether manual Excel changes were already synced.

**Block fields (Phase 2 TG wiring; Phase 1 library `build_manual_snapshot_health_report()`):**

| Field | Purpose |
|-------|---------|
| `current_dropbox_rev` | Live rev from `get_dropbox_file_rev` |
| `last_synced_dropbox_rev` | From `we_registry_meta` |
| `current_snapshot_hash_short` | First 12 chars of hash if computable |
| `last_successful_sync_at` | ISO timestamp |
| `snapshot_age_sec` | now − last success |
| `active_hold_rows` | COUNT active in PG |
| `active_otlezka_rows` | COUNT active in PG |
| `last_sync_status` | success / failed / skipped_rev / skipped_hash |
| `stale` | age > threshold or rev drift |
| `failed` | last status failed |

**Purpose:** operator can tell if manual edits are already seen by the system.

---

## Feature Flags (Phase 1)

| Env | Default | Behavior |
|-----|---------|----------|
| `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | `0` | `1` = modules callable; still requires Phase 2 wiring for pre-run gate |
| `DROPBOX_WALLET_EDITOR_PATH` | unset | **Existing** env; manual sync reads `hold`/`Отлёжка` from this workbook |
| `DATABASE_URL` | — | Required for PG sync |

**Not introduced in Phase 1:** `DROPBOX_WALLET_EDITOR_MANUAL_PATH` (deferred; split/rename only if needed post-migration).

---

## Known Risks

| Риск | Источник | Вероятность | Митигация |
|------|----------|-------------|-----------|
| R1 Schema migration on prod | new tables | med | DDL in `schema.sql`; apply via ops; flag off until verified |
| R2 False sync skip | rev/hash bug | med | exhaustive unit tests; audit `manual_sync_runs` |
| R3 Legacy combined xlsx migration | ops | med | Phase 2 bootstrap; separate cleanup Task with backup |
| R4 Multi-instance stale meta | Railway | low | PG meta, not in-process state |
| R5 Отлёжка sheet missing policy dispute | design | med | documented proposal § Отлёжка policy; Architect sign-off |
| **R6 Legacy stale sheets in workbook** | migration | **med** | Until cleanup Task complete, `all_results`/`runs` may remain in Dropbox file; manual sync **must ignore**; ops must not treat them as SoT |
| R7 Operator confuses legacy sheets with live data | migration | med | health/export from PG only; README in export (Phase 3); cleanup Task |

---

## Success Criteria

### Phase 1 (this Task)

- [x] `TASK-2026-07-01-01_impact.md` complete with verdict `proceed` or `proceed with caution`
- [x] `schema.sql` extended with `we_registry_hold`, `we_registry_otlezka`, `we_registry_manual_sync_runs`
- [x] `manual_snapshot.py`: validate + hash per contract § Normalized snapshot
- [x] `manual_sync.py`: rev skip, hash skip, sync transaction, failed validation preserves last good state
- [x] `manual_sync_state.py`: meta keys + `build_manual_snapshot_health_report()` (library)
- [x] `RunSnapshotBinding` dataclass for I-MAN-10 (no worker wiring)
- [x] Unit tests pass (see § Required tests)
- [x] `WALLET_EDITOR_MANUAL_SYNC_ENABLED=0` default — no production behavior change
- [x] No changes to `worker.py`, `wallet_editor_hold.py`, `auto_enable`, `tg_commands` export path

### Program (follow-on tasks — not Phase 1)

- [ ] Pre-run gate wired (Phase 2)
- [ ] Readers on PG (Phase 2)
- [ ] `/registry_sync_manual` + `/registry_export` TG-only (Phase 3)
- [ ] Deprecate combined registry Dropbox writes (Phase 4)
- [ ] Dropbox workbook cleanup — backup, verify PG, operator approval (Phase 5)

---

## Required tests (Phase 1)

| Test | Assert |
|------|--------|
| Same normalized data → same hash | stable |
| Row reorder → same hash | stable |
| card/partner trim/casefold → same hash | stable |
| comment change → hash changes | |
| `Дата добавления` change → hash changes (if column present) | |
| `Полные дни` change → hash changes | |
| duplicate hold key → `ManualSyncValidationError` | |
| duplicate Отлёжка partner → validation error | |
| rev same → `download_file_with_rev` not called | |
| rev changed + same snapshot → no hold/otlezka row mutation; meta rev updated | |
| rev changed + changed snapshot → sync upsert + deactivate | |
| invalid workbook → PG unchanged (mock store) | |
| workbook with legacy `all_results`/`runs` + unchanged hold/otlezka → same hash | legacy sheets ignored |
| edits only on legacy `all_results` sheet → hash unchanged | |

---

## Out Of Scope (Phase 1)

- Pre-run gate in `worker.py` / auto-enable (Phase 2 — `TASK-2026-07-01-02` proposed)
- `wallet_editor_hold` / auto-enable planning read from PG
- `/registry_sync_manual` Telegram command
- `/registry_export` redesign (TG-only, no Dropbox)
- Removal of `schedule_excel_export` / legacy Dropbox registry append path
- `CP-dev_task` creation (Architect before `ready`)
- KB update (`contracts.md`, `decisions.md` E-WE-23) — post-merge after program cutover
- Production cutover / env rollout on Railway
- Playwright / Antares changes
- **Deletion or cleanup** of `all_results` / `runs` from Dropbox workbook (separate ops Task)
- Introduction of `DROPBOX_WALLET_EDITOR_MANUAL_PATH`

---

## Workflow decisions

| Decision | Value |
|----------|-------|
| Impact required? | **yes** (P-WE, new PG tables, medium risk) |
| Context Pack required? | **yes** at `ready` |
| Phase 1 status target | **`ready`** — implement Phase 1 library |

---

## Follow-on tasks (proposed IDs)

| ID | Scope |
|----|-------|
| TASK-2026-07-01-02 | Pre-run gate + snapshot isolation per run |
| TASK-2026-07-01-03 | Reader migration (hold, auto-enable, lifecycle, health) |
| TASK-2026-07-01-04 | `/registry_export` TG-only + `/registry_sync_manual` |
| TASK-2026-07-01-05 | Deprecate combined Dropbox registry write path |
| TASK-2026-07-01-06 | Dropbox workbook cleanup: PG verify, backup, operator approval, leave `hold`+`Отлёжка` only |

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Task created from discovery + design sessions |
| 2026-07-01 | Initiator + Cursor | D7/D12: reuse `DROPBOX_WALLET_EDITOR_PATH`; legacy sheets ignored; no auto-cleanup in Phase 1 |
| 2026-07-01 | Initiator + Architect | All decisions approved; I-MAN-10 + health block; status → `ready` |
