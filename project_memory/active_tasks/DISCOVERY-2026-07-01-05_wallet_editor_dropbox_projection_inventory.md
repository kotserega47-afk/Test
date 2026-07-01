# Discovery — WalletEditor Phase 5: Dropbox registry projection inventory

| Мета | Значение |
|------|----------|
| **Тип** | Investigation only (no code changes) |
| **Дата** | 2026-07-01 |
| **Связанная Task** | TASK-2026-07-01-05 (draft) |
| **Программа** | WalletEditor Registry v2 — Phases 1–4 complete |
| **Цель** | Инвентаризация оставшихся Dropbox registry projection/write paths перед Phase 5 |

---

## Executive summary

Phases 1–4 перевели manual input, runtime readers и `/registry_export` на PostgreSQL + Telegram. **Hot path postgres mode** всё ещё после каждого успешного append/patch/refresh вызывает **`schedule_excel_export`** → **`export_registry_workbook_to_dropbox`** (download + upload `all_results`/`runs` в Dropbox, preserve `hold`/`Отлёжка` flags).

Дополнительно **`append_run_to_dropbox_registry`** требует `DROPBOX_WALLET_EDITOR_PATH` **даже в postgres mode** (ранний return без append, если path unset).

Legacy branch **`WALLET_EDITOR_REGISTRY_SOURCE=excel`** по-прежнему пишет registry напрямую в Dropbox workbook (full online path).

**Рекомендация:** Phase 5 implementation — flag-disable projection hot path, decouple registry writes from Dropbox path requirement, migrate CLI на `RegistryExportBuilder`, обновить health. **Ops sheet cleanup** — отдельная задача.

---

## 1. Remaining Dropbox write/projection inventory

| ID | Path | Module | Trigger | Writes to Dropbox? | Classification |
|----|------|--------|---------|-------------------|----------------|
| P1 | `schedule_excel_export` | `excel_export.py` | append/patch/refresh success when `registry_source=postgres` | **Yes** (async thread) | **Hot path** |
| P2 | `export_registry_workbook_to_dropbox` | `excel_export.py` | P1, CLI, lifecycle refresh apply | **Yes** | Hot + tool |
| P3 | `_append_attempt` (excel branch) | `wallet_editor_registry.py` | `registry_source=excel` | **Yes** | Legacy rollback |
| P4 | `_patch_attempt` (excel branch) | `wallet_editor_registry.py` | `registry_source=excel` | **Yes** | Legacy rollback |
| P5 | `_refresh_attempt` (excel branch) | `wallet_editor_registry_refresh.py` | `registry_source=excel` | **Yes** | Legacy rollback |
| P6 | `refresh_lifecycle_fields` apply | `refresh_lifecycle_fields.py` | CLI `--apply` + rows changed | **Yes** (sync call) | Tool-only |
| P7 | CLI `export_wallet_editor_registry.py` | `tools/` | manual ops | **Yes** | Tool-only |
| P8 | `append_run_to_dropbox_registry` gate | `wallet_editor_registry.py` L214–220 | any async append | **Blocks** if path unset | Hot path coupling |
| P9 | `patch_enable_results_in_dropbox_registry` gate | `wallet_editor_registry.py` L661–668 | auto-enable B2 | **Blocks** if path unset | Hot path coupling |

**Not registry projection (keep after Phase 5):**

| ID | Path | Purpose |
|----|------|---------|
| M1 | `manual_sync.py` / `manual_snapshot.py` | Read `hold`/`Отлёжка` from Dropbox → PG |
| M2 | `hold_loader.py` | Dropbox fallback when `MANUAL_READERS_SOURCE=dropbox` |
| M3 | `wallet_editor_hold.load_hold_pairs_from_dropbox` | Legacy readers fallback |
| M4 | `get_dropbox_file_rev` | Manual sync rev metadata / health |

**Already PG-only (Phase 4):**

| Path | Notes |
|------|-------|
| `/registry_export` | `RegistryExportBuilder` → Telegram; no Dropbox IO |
| `append_attempt_postgres` / `patch_attempt_postgres` / `refresh_attempt_postgres` | PG write only; projection is separate (P1) |

---

## 2. Call graphs

### 2.1 Hot path — disable → registry append (postgres mode, prod default)

```text
worker._run_disable_task
  → prepare_registry_outbox_and_schedule
  → append_run_to_dropbox_registry
       ├─ requires DROPBOX_WALLET_EDITOR_PATH (P8) ← coupling
       ├─ _append_attempt → append_attempt_postgres (PG commit)
       ├─ update_outbox_status(SYNCED)
       └─ schedule_excel_export(operation=append)  ← P1
            └─ export_registry_workbook_to_dropbox  ← P2
                 ├─ load_registry_frames_from_postgres
                 ├─ download_file_with_rev (preserve hold/otlezka flags)
                 ├─ save_registry_workbook (all_results + runs)
                 └─ upload_file_if_rev
```

### 2.2 Hot path — auto-enable B2 patch (postgres mode)

```text
execute_enable_batch → patch_enable_results_in_dropbox_registry
  ├─ requires DROPBOX_WALLET_EDITOR_PATH (P9)
  ├─ _patch_attempt → patch_attempt_postgres (PG)
  └─ schedule_excel_export(operation=patch) → P2
```

### 2.3 Hot path — lifecycle refresh job (postgres mode)

```text
refresh_wallet_editor_registry_lifecycle
  ├─ requires DROPBOX_WALLET_EDITOR_PATH
  ├─ _refresh_attempt → refresh_attempt_postgres (PG lifecycle update)
  └─ schedule_excel_export(operation=refresh) → P2
```

### 2.4 Legacy excel source rollback (`WALLET_EDITOR_REGISTRY_SOURCE=excel`)

```text
_append_attempt / _patch_attempt / _refresh_attempt (non-postgres branches)
  → download → mutate → save_registry_workbook → upload_file_if_rev
  → schedule_mirror_batch (if mirror enabled; separate from P1)
```

### 2.5 Tool paths

```text
tools/export_wallet_editor_registry.py → export_registry_workbook_to_dropbox (P2)

tools/refresh_registry_lifecycle_fields.py --apply
  → refresh_lifecycle_fields (PG patch)
  → export_registry_workbook_to_dropbox if rows_changed (P6)
```

---

## 3. Hot path vs tool-only classification

| Surface | Postgres write | Dropbox projection | Dropbox path required today |
|---------|----------------|-------------------|----------------------------|
| Disable append (async) | Yes | Yes (P1) | **Yes** (P8) |
| Auto-enable patch | Yes | Yes (P1) | **Yes** (P9) |
| Lifecycle refresh job | Yes | Yes (P1) | Yes |
| `/registry_export` | Read-only | **No** | **No** |
| Manual sync | PG write | **No** | Yes (by design) |
| CLI export | Read PG | Yes (P7) | Yes |
| CLI lifecycle refresh apply | PG write | Optional (P6) | Yes |

---

## 4. `schedule_excel_export` detail

**Definition:** `integrations/wallet_editor_registry_db/excel_export.py`

| Property | Value |
|----------|-------|
| Call sites | `wallet_editor_registry.py` L346–348 (append), L697–699 (patch), `wallet_editor_registry_refresh.py` L445–447 (refresh) |
| Guard | `registry_source_is_postgres()` inside `schedule_excel_export` |
| Disable flag today | **None** |
| Failure handling | Fire-and-forget daemon thread; `record_excel_export_failure`; event `wallet_editor_registry_excel_export_failed` |
| Success handling | `record_excel_export_success` |
| Affects outbox? | **No** — outbox marked SYNCED before export scheduled |

**Tests expecting `schedule_excel_export`:**

- `test_wallet_editor_registry_postgres_source.py` — `test_postgres_success_excel_export_fail_outbox_synced`, replay test mocks
- Implicit via append success paths

---

## 5. `export_registry_workbook_to_dropbox` detail

| Caller | Sync/async |
|--------|------------|
| `schedule_excel_export` | async thread |
| `tools/export_wallet_editor_registry.py` | sync CLI |
| `refresh_lifecycle_fields.py` (apply + rows_changed) | sync |

**Behavior:** PG history → download existing workbook → overwrite `all_results`/`runs` → preserve `hold`/`Отlёжka` sheet presence → upload.

**After Phase 5 removal from hot path:** retain as **legacy emergency tool** or replace CLI with `RegistryExportBuilder` + local file write.

---

## 6. Outbox / replay semantics

### Current success criteria (postgres mode)

| Step | Outbox status | Depends on Dropbox upload? |
|------|---------------|---------------------------|
| PG append/patch succeeds | `SYNCED` | **No** |
| `schedule_excel_export` fails | stays `SYNCED` | N/A |
| PG append fails | `FAILED` / retry | N/A |

**Confirmed test:** `test_postgres_success_excel_export_fail_outbox_synced` — outbox SYNCED even when export fails.

### Replay (`replay_pending_outbox_records`)

```text
replay → append_run_to_dropbox_registry → _append_attempt (postgres)
```

- Replay success = PG append success + outbox SYNCED
- **Not** tied to excel export success
- **Blocker today:** replay/append skipped entirely if `DROPBOX_WALLET_EDITOR_PATH` unset (P8), even though PG write does not need Dropbox

### Phase 5 recommendation

- When projection disabled: `append_run_to_dropbox_registry` must **not** require Dropbox path for postgres append
- Outbox semantics **unchanged**: SYNCED on PG write success
- `/registry_health`: excel export failures should not degrade when projection disabled

---

## 7. Tests that must change (Phase 5)

### Expect `schedule_excel_export` / Dropbox upload

| File | Notes |
|------|-------|
| `test_wallet_editor_registry_postgres_source.py` | excel export fail tests; schedule_excel_export mocks |
| `test_export_wallet_editor_registry.py` | Full Dropbox export CLI — rewrite to RegistryExportBuilder or legacy flag |
| `test_refresh_registry_lifecycle_fields.py` | `excel_exported` assertion |
| `test_wallet_editor_registry_refresh.py` | `upload_file_if_rev` mocks (excel refresh branch) |
| `test_wallet_editor_dropbox_registry.py` | Excel-source append tests — keep under rollback flag |
| `test_wallet_editor_registry_enable_patch.py` | Excel patch upload |
| `test_wallet_editor_registry_outbox.py` | upload mocks |
| `test_wallet_editor_registry_timeout.py` | upload mocks |
| `test_wallet_editor_registry_db_shadow.py` | upload mocks |

### New tests needed

- Projection disabled: append/patch/refresh **do not** call `schedule_excel_export`
- Append succeeds with `DROPBOX_WALLET_EDITOR_PATH` unset when projection off (manual sync path separate)
- `/registry_health` shows projection disabled; excel export failures ignored
- CLI export writes local file via `RegistryExportBuilder`

### Legacy tests to retain (rollback flag `registry_source=excel` or projection enabled)

- `test_wallet_editor_dropbox_registry.py` (excel SoT behavior)
- Shadow/mirror tests if excel branch kept

---

## 8. Rollback options

| Level | Action | Effect |
|-------|--------|--------|
| R1 | `WALLET_EDITOR_REGISTRY_DROPBOX_PROJECTION_ENABLED=1` (proposed) | Re-enable P1 hot path |
| R2 | `WALLET_EDITOR_REGISTRY_SOURCE=excel` | Full legacy online Excel registry |
| R3 | CLI `export_wallet_editor_registry.py` | Manual rebuild Dropbox workbook from PG |
| R4 | `/registry_export` | TG document from PG (Phase 4; always available) |
| R5 | Code revert deploy | Restore pre-Phase-5 behavior |

**Prod cutover:** disable projection flag only after smoke: append, patch, refresh, export, manual sync, health.

---

## 9. Recommended minimal Phase 5 implementation scope

### In scope (TASK-2026-07-01-05)

1. **New flag** `WALLET_EDITOR_REGISTRY_DROPBOX_PROJECTION_ENABLED` (default **`0`** after deploy; document rollback `=1`).
2. **Remove P1 from hot path** when flag off — no `schedule_excel_export` after append/patch/refresh.
3. **Decouple P8/P9** — postgres append/patch allowed without `DROPBOX_WALLET_EDITOR_PATH` when projection off; manual sync still requires path.
4. **CLI** `tools/export_wallet_editor_registry.py` → `RegistryExportBuilder` + `--output` local file (no Dropbox upload).
5. **`refresh_lifecycle_fields`** — remove P6 export or gate behind projection flag.
6. **`/registry_health`** — show `dropbox_projection_enabled`; do not degrade on excel export failures when disabled.
7. **Keep** excel append/patch/refresh branches behind `registry_source=excel` for emergency rollback (P3–P5).
8. **Deprecate** `export_registry_workbook_to_dropbox` in hot path docs; keep function for rollback flag only.

### Out of scope (separate ops Task)

- Physical deletion of `all_results`/`runs` sheets from Dropbox workbook
- Prod env rollout checklist execution
- Removing `registry_source=excel` code entirely

### Follow-on: TASK-2026-07-01-06 (proposed)

Ops workbook cleanup after PG verification.

---

## 10. Risks and blockers

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| 1 | Ops still use Dropbox workbook as registry view | **High** | Train on `/registry_export`; CLI local export |
| 2 | External process reads `all_results` from Dropbox | **High** | Inventory consumers before cutover |
| 3 | `append_run_to_dropbox_registry` name/path gate confuses operators | Med | Decouple + log clearly |
| 4 | Health degraded on stale excel_export_state | Med | Reset/ignore when projection off |
| 5 | `registry_source=excel` env typo in prod | Med | Health shows registry_source |
| 6 | Manual sync still needs Dropbox path — ops think registry dead | Med | Health distinguishes manual vs projection |
| 7 | Replay blocked without path today | Med | Fix P8 in Phase 5 |

**Blockers:** none technical for draft; **prod cutover** requires ops sign-off on external Dropbox consumers.

---

## 11. Ops cleanup plan draft (TASK-2026-07-01-06)

### Preconditions

- [ ] `WALLET_EDITOR_REGISTRY_SOURCE=postgres` in prod ≥ 30 days
- [ ] `WALLET_EDITOR_REGISTRY_DROPBOX_PROJECTION_ENABLED=0` in prod
- [ ] `/registry_health`: `processed_without_rows_count=0`, no corrupted processed_run_ids
- [ ] `/registry_export` smoke OK
- [ ] Manual sync smoke OK (`hold`/`Отлёжка` edits propagate to PG)
- [ ] Inventory: no automated jobs read `all_results`/`runs` from Dropbox

### Backup

1. Run `/registry_export` → save TG document locally
2. Download current `DROPBOX_WALLET_EDITOR_PATH` via Dropbox UI or CLI
3. Store backup: `wallet_editor_backup_YYYYMMDD.xlsx` off-Dropbox

### Cleanup procedure

1. Open backup copy (not live file first — dry run)
2. Delete sheets: `all_results`, `runs`, any legacy `README`/`sync_status`/export sheets
3. **Keep only:** `hold`, `Отлёжка` (rename verify)
4. Upload cleaned file to same path (or replace via Dropbox)
5. Run manual sync gate smoke
6. Verify `/registry_health` manual block

### Rollback

- Restore backup file to `DROPBOX_WALLET_EDITOR_PATH`
- Set `WALLET_EDITOR_REGISTRY_DROPBOX_PROJECTION_ENABLED=1` if needed
- Re-run manual sync

---

## 12. Target architecture (post Phase 5)

```text
WalletEditor operation → PostgreSQL write → done

DROPBOX_WALLET_EDITOR_PATH → hold + Отлёжка only (ManualSync ingest)

/registry_export → PostgreSQL → RegistryExportBuilder → Telegram
```

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor | Discovery inventory for Phase 5 |
