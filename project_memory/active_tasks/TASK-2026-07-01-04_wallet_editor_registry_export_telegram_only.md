# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-07-01-04 |
| **Статус** | ready |
| **Приоритет** | medium |
| **KB версия** | v1.8 |
| **Связанные артефакты** | `TASK-2026-07-01-04_wallet_editor_registry_export_telegram_only_impact.md`, `CP-dev_task-TASK-2026-07-01-04-20260701.md`, `TASK-2026-07-01-01_*` … `TASK-2026-07-01-03_*` |
| **Impact** | required — см. `_impact.md` |
| **Context Pack** | `CP-dev_task-TASK-2026-07-01-04-20260701.md` |
| **Depends on** | TASK-2026-07-01-01 (PG manual store); TASK-2026-07-01-03 (PG hold/Отлёжка tables) |
| **Program** | WalletEditor Registry v2 — manual Excel → PG sync → PG-only runtime → TG-only export |

---

## Goal

Переделать **`/registry_export`**: workbook строится **единым PG-backed `RegistryExportBuilder`**, доставка в Telegram — **отдельный слой**. Dropbox не участвует в export chain.

**Phase 4:** shared builder + TG delivery for `/registry_export` + tests. **Не** Phase 5, **не** удаление Dropbox write paths, **не** CLI rewrite.

---

## Target architecture

```text
PostgreSQL
  ↓
RegistryExportBuilder  → workbook artifact (.xlsx)
  ↓
Telegram delivery (/registry_export)
```

**D-EXPORT-01:** Workbook building and delivery are separate concerns. `/registry_export` uses the shared PG-backed export builder and Telegram delivery. **No Telegram-specific builder.**

Future delivery (out of Phase 4): CLI save-to-file, local file — same `RegistryExportBuilder`.

---

## Decision D-EXPORT-01

| Layer | Responsibility |
|-------|----------------|
| `RegistryExportBuilder` | Load export-ready data from PostgreSQL; build one valid xlsx; return file artifact + summary |
| Delivery (`/registry_export`) | Take artifact → `send_document` to requester chat |
| `export_registry_workbook_to_dropbox()` | **Unchanged** — background Dropbox projection (I-MAN-06) |

---

## RegistryExportBuilder contract

| Responsibility | Detail |
|----------------|--------|
| Data source | PostgreSQL only (`all_results`, `runs`, `hold`, `Отлёжка`, meta) |
| Output | Workbook path + `RegistryExportSummary` (delivery-agnostic) |
| Sheets | `all_results`, `runs`, `hold`, `Отлёжка`, `README`, `sync_status` |
| I-MAN-11 | **Does not apply** — missing sync → degraded flag in summary, export proceeds |
| DB writes | **None** |

### README sheet (required text)

```text
WalletEditor Registry Export

This workbook is a generated report.

Source of truth:
- Registry history: PostgreSQL
- Hold: PostgreSQL
- Отлёжка: PostgreSQL

Do not edit this workbook.

To change Hold or Отлёжка, edit the operator workbook in Dropbox.

Generated at: <timestamp>
Manual snapshot hash: <hash short>
Last manual sync: <timestamp or none>
```

### Filename

`wallet_editor_export_YYYYMMDD_HHMMSS_MSK.xlsx`

---

## `/registry_export` delivery

- ACL: existing `_guard_or_deny`
- Requires: `DATABASE_URL`, `WALLET_EDITOR_REGISTRY_SOURCE=postgres`
- **No** `DROPBOX_WALLET_EDITOR_PATH`, download, upload
- `RegistryExportBuilder.build()` → `reply_document` to `update.effective_chat.id`
- Summary message: row counts, snapshot hash short, last sync, degraded warning if applicable

---

## Constraints

- **I-MAN-06:** Keep `schedule_excel_export` / Dropbox write paths.
- **I-MAN-08:** Export must not read Dropbox.
- **I-MAN-09:** No legacy sheet cleanup.
- **D-EXPORT-01:** Single builder; delivery separate.
- **CLI:** `tools/export_wallet_editor_registry.py` **out of scope** Phase 4; builder must be reusable for future CLI.

---

## Affected Modules

| Модуль | Действие |
|--------|----------|
| **new** `registry_export_builder.py` | `RegistryExportBuilder`, `RegistryExportArtifact`, `RegistryExportSummary` |
| `registry_xlsx.py` | `save_registry_export_workbook()` — 6 sheets |
| `manual_readers.py` | `load_hold_otlezka_frames_for_export()` (no I-MAN-11 gate) |
| `excel_export.py` | Keep Dropbox export; optional `format_registry_export_summary` |
| `tg_commands.py` | Builder + TG delivery only |
| `tests/unit/test_registry_export_builder.py` | Builder + sheets (new) |
| `tests/unit/test_tg_registry_export.py` | TG delivery path |

---

## Success Criteria

- [x] `RegistryExportBuilder` — single PG-backed workbook builder
- [x] D-EXPORT-01: no Telegram-specific builder
- [x] `/registry_export` uses builder + TG delivery; no Dropbox IO
- [x] All 6 sheets including README with required text
- [x] Missing manual sync → degraded warning, export not blocked
- [x] Export failure does not mutate DB
- [x] `schedule_excel_export` unchanged
- [x] All § Required tests pass

---

## Required tests

| # | Test | Assert |
|---|------|--------|
| 1 | `RegistryExportBuilder` | PG-only data load |
| 2 | sheets | all 6 present |
| 3 | no Dropbox download | export path |
| 4 | no Dropbox upload | export path |
| 5 | TG delivery | document to requester chat |
| 6 | summary | counts + snapshot info |
| 7 | DB read failure | TG error |
| 8 | build failure | TG error |
| 9 | TG send failure | logged + error |
| 10 | missing manual sync | degraded warning; no DB mutation |
| 11 | legacy Dropbox sheets | irrelevant |
| 12 | export failure | DB unchanged |

```bash
pytest tests/unit/test_registry_export_builder.py \
  tests/unit/test_tg_registry_export.py \
  tests/unit/test_export_wallet_editor_registry.py \
  tests/unit/test_wallet_editor_registry_postgres_source.py -q
```

---

## Out Of Scope

Phase 5, `schedule_excel_export` removal, CLI TG migration, manual sync/gate changes, KB update.

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Task created |
| 2026-07-01 | Cursor | D-EXPORT-01 + RegistryExportBuilder; status → ready |
