# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-07-01-05 |
| **Статус** | ready |
| **Приоритет** | medium |
| **KB версия** | v1.8 |
| **Связанные артефакты** | `DISCOVERY-2026-07-01-05_wallet_editor_dropbox_projection_inventory.md`, `TASK-2026-07-01-05_wallet_editor_deprecate_dropbox_registry_projection_impact.md`, `CP-dev_task-TASK-2026-07-01-05-20260701.md`, Phases 1–4 Tasks |
| **Impact** | required — см. `_impact.md` |
| **Context Pack** | `CP-dev_task-TASK-2026-07-01-05-20260701.md` |
| **Depends on** | TASK-2026-07-01-04 (TG export + `RegistryExportBuilder`); Phases 1–3 in prod |
| **Program** | WalletEditor Registry v2 — finalize PostgreSQL registry architecture |

---

## Goal

**Полностью удалить** runtime-зависимость от Dropbox registry projection. PostgreSQL — единственный SoT для registry history; Dropbox workbook — только manual input (`hold`, `Отлёжка`); export — только `/registry_export` → Excel → Telegram.

**Не disable. Не legacy flags. Удалить из runtime.**

---

## Final architecture (approved)

```text
PostgreSQL (SoT)
  ├── registry history (all_results, runs)
  ├── lifecycle runtime
  └── hold / Отлёжка runtime (after manual sync)

DROPBOX_WALLET_EDITOR_PATH
  └── operator manual workbook only (hold + Отлёжка)
      └── ManualSync ingest

/registry_export
  └── RegistryExportBuilder → Excel → Telegram

CLI export
  └── RegistryExportBuilder → local file (no Dropbox)

Rollback
  └── Git / deploy rollback
# TASK-2026-07-01-05 — rollback via Git/deploy only (excel source removed)
```

**Removed from runtime:**

- `schedule_excel_export`
- `export_registry_workbook_to_dropbox`
- `excel_export.py`, `excel_export_state.py`
- projection health / degraded signals
- hot-path Dropbox writes for `all_results` / `runs`

---

## Success criteria

- [x] Discovery report complete
- [x] Impact verdict `proceed`
- [x] Hot path append/patch/refresh do not call projection
- [x] Postgres append/patch work without `DROPBOX_WALLET_EDITOR_PATH` when manual readers=postgres
- [x] CLI writes local file via `RegistryExportBuilder`
- [x] `/registry_health` shows projection DISABLED + export Telegram only
- [x] Outbox SYNCED semantics unchanged
- [x] Legacy `registry_source=excel` branch preserved
- [x] Required tests pass (43 in Phase 5 suite)
- [x] Ops sheet cleanup **not** done in code

---

## Required tests (Phase 5)

| # | Test | Status |
|---|------|--------|
| 1 | append postgres — no projection | pass |
| 2 | append postgres without Dropbox path | pass |
| 3 | patch postgres — no Dropbox upload | pass |
| 4 | refresh postgres — no Dropbox upload | pass |
| 5 | replay without projection | pass |
| 6 | CLI uses `RegistryExportBuilder` | pass |
| 7 | `/registry_export` unchanged | pass |
| 8 | manual sync unchanged | pass (existing suite) |
| 9 | health — no projection warnings | pass |
| 10 | export independent of Dropbox | pass |

```bash
pytest tests/unit/test_wallet_editor_registry_projection_removed.py \
  tests/unit/test_wallet_editor_registry_postgres_source.py \
  tests/unit/test_export_wallet_editor_registry.py \
  tests/unit/test_registry_export_builder.py \
  tests/unit/test_tg_registry_export.py \
  tests/unit/test_refresh_registry_lifecycle_fields.py -q
```

**Result:** 43 passed (2026-07-01).

---

## Changed files

| File | Change |
|------|--------|
| `integrations/wallet_editor_registry.py` | Remove projection; decouple Dropbox path; health |
| `integrations/wallet_editor_registry_refresh.py` | Remove projection; conditional Dropbox gate |
| `integrations/wallet_editor_registry_db/refresh_lifecycle_fields.py` | Remove export block |
| `integrations/wallet_editor_registry_db/registry_export_builder.py` | README + summary text |
| `tools/export_wallet_editor_registry.py` | `RegistryExportBuilder` + `--output` |
| `tools/refresh_registry_lifecycle_fields.py` | Remove `--no-export` |
| `tests/unit/test_wallet_editor_registry_projection_removed.py` | **new** |
| `tests/unit/test_export_wallet_editor_registry.py` | rewritten |
| `tests/unit/test_wallet_editor_registry_postgres_source.py` | updated |
| `tests/unit/test_refresh_registry_lifecycle_fields.py` | updated |
| `tests/unit/test_registry_export_builder.py` | README assertions |

## Deleted runtime components

- `integrations/wallet_editor_registry_db/excel_export.py`
- `integrations/wallet_editor_registry_db/excel_export_state.py`

---

## Out of scope

- Ops deletion of `all_results`/`runs` sheets in Dropbox (→ TASK-2026-07-01-06)
- Removing `registry_source=excel` emergency branch
- Playwright / manual sync / pre-run gate changes

---

## Follow-on: TASK-2026-07-01-06 (ops)

1. Verify PG completeness
2. Backup Dropbox workbook
3. Delete generated sheets; keep `hold` + `Отлёжка`
4. Document rollback

---

## Confirmation

```text
Runtime больше не пишет registry history в Dropbox.

Dropbox workbook используется только как manual workbook
(hold + Отлёжка).

Единственный способ получить registry —

/registry_export → Telegram.
```

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Task + discovery — projection inventory |
| 2026-07-01 | Cursor (ready) | Final architecture: full removal, no flags; implemented |
