# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-07-01-03 |
| **Статус** | ready |
| **Приоритет** | medium |
| **KB версия** | v1.8 |
| **Связанные артефакты** | `TASK-2026-07-01-03_wallet_editor_manual_readers_to_postgres_impact.md`, `CP-dev_task-TASK-2026-07-01-03-20260701.md`, `TASK-2026-07-01-01_*`, `TASK-2026-07-01-02_*` |
| **Impact** | required — см. `_impact.md` |
| **Context Pack** | `CP-dev_task-TASK-2026-07-01-03-20260701.md` |
| **Depends on** | TASK-2026-07-01-01 (sync library + schema v2); TASK-2026-07-01-02 (pre-run gate + binding) |
| **Program** | WalletEditor Registry v2 — manual Excel → PG sync → PG-only runtime |

---

## Goal

Перевести **runtime readers** `hold` / `Отлёжка` на PostgreSQL, чтобы после обязательного manual sync gate enforcement и planning работали с **тем же SoT**, который был синхронизирован перед стартом запуска.

**Phase 3 (этот Task):** reader migration + rollback flag + health visibility. **Не** включает TG-only export, удаление Dropbox registry writes, cleanup legacy sheets.

---

## Target architecture

```text
DROPBOX_WALLET_EDITOR_PATH
  ↓ только manual sheets hold / Отлёжка
ManualSync (ensure_manual_snapshot_current)
  ↓
PostgreSQL (we_registry_hold, we_registry_otlezka)
  ↓
runtime readers (hold enforcement, auto-enable planning, lifecycle refresh, health)
```

**Invariant I-MAN-08:** только **ManualSync** имеет право читать manual sheets из Dropbox. Runtime readers после Phase 3 в режиме `postgres` **не** скачивают workbook для `hold`/`Отлёжка`.

**Invariant I-MAN-11:** PG manual readers may be used **only after at least one successful manual sync** (`success`, `skipped_hash`, or `skipped_rev` with `last_manual_snapshot_hash` + `last_manual_sync_at` in meta). If `WALLET_EDITOR_MANUAL_READERS_SOURCE=postgres` and no successful manual sync exists:

| Consumer | Behavior |
|----------|----------|
| Hold enforcement | **fail-closed** (`HoldPairsSnapshot.unavailable`) |
| Auto-enable planning | **fail-closed** (clear error) |
| Lifecycle refresh | **fail-closed** (`PERMANENT`) |
| `/registry_health` | **DEGRADED:** `no successful manual sync` |

---

## Business Context

Phase 2 устранил drift на границе запуска (gate + `RunSnapshotBinding`), но readers по-прежнему читали `hold`/`Отлёжка` из Dropbox. Phase 3 замыкает контур: **sync → PG → readers**.

Контур: **P-WE**, **R6**.

---

## Desired Behavior (Phase 3)

### Reader migration map

| # | Consumer | Phase 3 behavior (`MANUAL_READERS_SOURCE=postgres`) |
|---|----------|------------------------------------------------------|
| 1 | `wallet_editor_hold.py` | Active hold pairs из `we_registry_hold`; fail-closed без successful sync |
| 2 | `load_registry_frames_for_planning()` | `all_results`/`runs` из PG; `hold`/`Отлёжка` из PG tables |
| 3 | Lifecycle refresh | `hold`/`Отлёжка` DataFrames из PG |
| 4 | `engine.run` hold check | Via `load_hold_pairs_snapshot()` after gate |
| 5 | `/registry_health` | `manual_readers_source` + DEGRADED when no successful sync |

### Feature flags

| Env | Default (Phase 3) | Purpose |
|-----|-------------------|---------|
| `WALLET_EDITOR_MANUAL_READERS_SOURCE` | `dropbox` | `postgres` = PG readers for hold/otlezka |
| `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | `0` (existing) | Gate; should be `1` before prod cutover |
| `WALLET_EDITOR_REGISTRY_SOURCE` | `postgres` (prod) | History SoT — unchanged |

**Rollout:** schema v2 → `MANUAL_SYNC_ENABLED=1` smoke → `MANUAL_READERS_SOURCE=postgres` staging → prod.

---

## Constraints

- **I-MAN-01:** Validation/sync failure must not corrupt last-good PG hold/otlezka.
- **I-MAN-08:** Only ManualSync reads manual sheets from Dropbox.
- **I-MAN-10:** Readers use data consistent with binding at run start.
- **I-MAN-11:** PG readers require at least one successful manual sync (fail-closed otherwise).
- **Phase 3 OUT:** TG-only export (Phase 4); `edit_wallet` unchanged.

---

## Success Criteria

- [x] Impact verdict `proceed` / `proceed with caution`
- [x] `WALLET_EDITOR_MANUAL_READERS_SOURCE` with default `dropbox`
- [x] All § Reader migration map implemented behind flag
- [x] I-MAN-11 fail-closed when postgres readers without successful sync
- [x] Dropbox hold/otlezka read only in ManualSync + explicit dropbox fallback
- [x] `/registry_health` shows manual readers source + DEGRADED when no sync
- [x] All § Required tests pass (postgres + dropbox modes)
- [x] `edit_wallet` unaffected
- [x] No export redesign; no Dropbox write removal

---

## Required tests (Phase 3)

| # | Test | Assert |
|---|------|--------|
| 1 | hold enforcement, readers=postgres | reads active pairs from PG |
| 2 | hold enforcement, readers=postgres | no Dropbox download |
| 3 | hold enforcement, readers=dropbox | legacy Dropbox path works |
| 4 | auto-enable planning, postgres | all_results/runs/hold/otlezka from PG |
| 5 | auto-enable planning, postgres | no registry workbook download |
| 6 | lifecycle refresh | hold/otlezka from PG |
| 7 | missing otlezka partner | warning, not block |
| 8 | gate OK + postgres readers | enforcement uses synced PG data |
| 9 | postgres readers, no sync meta | fail-closed / clear error (I-MAN-11) |
| 10 | `/registry_health` | shows manual readers source + DEGRADED |
| 11 | legacy sheets in Dropbox | no effect on PG reader mode |
| 12 | edit_wallet | unaffected |

**Run:**

```bash
pytest tests/unit/test_wallet_editor_manual_readers_pg.py \
  tests/unit/test_wallet_editor_hold_enforcement.py \
  tests/unit/test_wallet_editor_auto_enable_orchestrator.py -q
```

---

## Out Of Scope (Phase 3)

- `/registry_export` TG-only (TASK-2026-07-01-04)
- Removal of Dropbox registry writes
- Workbook cleanup
- `edit_wallet` gate or reader changes

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Task created — Phase 3 reader migration spec |
| 2026-07-01 | Cursor | I-MAN-11 invariant added; status → ready; Phase 3 implemented |
