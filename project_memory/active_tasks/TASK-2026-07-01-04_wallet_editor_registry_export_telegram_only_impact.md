# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-07-01-04 |
| **Связанная задача** | TASK-2026-07-01-04 |
| **KB версия** | v1.8 |
| **Триггер** | E-WE-22 behavior change; ops recovery path; eliminates export→Dropbox coupling |

---

## Current Runtime Behavior

**Phases 1–3 complete:** manual sync, pre-run gate, PG readers (with I-MAN-11), default readers `dropbox`.

**`/registry_export` today:**

```text
cmd_registry_export
  → requires DROPBOX_WALLET_EDITOR_PATH
  → export_registry_workbook_to_dropbox()
       → load_registry_frames_from_postgres()
       → download_file_with_rev(dropbox_path)  # preserve hold/otlezka
       → save_registry_workbook()
       → upload_file_if_rev()
  → format_export_registry_summary() → Telegram text only
```

Operators get a **text summary**; the actual xlsx lands in **Dropbox**, not in Telegram.

---

## Runtime Paths

| Путь | Today | Phase 4 |
|------|-------|---------|
| `/registry_export` | PG history + Dropbox hold/otlezka → **Dropbox upload** | PG all sheets → **TG send_document** |
| `schedule_excel_export` | PG → Dropbox (background) | **unchanged** |
| Append/patch postgres | commit → optional schedule export | **unchanged** |
| CLI `export_wallet_editor_registry.py` | Dropbox upload | **unchanged** |
| Manual sync | Dropbox ingest | **unchanged** |
| `/registry_health` | diagnostics | **unchanged** (optional: mention TG export in runbook) |

---

## Contracts Impact

| Контракт | Change | Breaking |
|----------|--------|----------|
| E-WE-22 `/registry_export` | Delivery: Dropbox → Telegram document | **yes** for ops expecting Dropbox file update |
| `DROPBOX_WALLET_EDITOR_PATH` for export | **no longer required** for `/registry_export` | **behavioral** — positive for DR |
| Export workbook sheets | +`README`, +`sync_status`; hold/otlezka from **PG** not Dropbox | additive |
| `contracts.md` Excel projection | `/registry_export` no longer part of Dropbox projection path | program alignment |
| I-MAN-08 | Export stops reading Dropbox manual sheets | **aligned** |

STALE_RISK: **S4** not affected.

---

## Pipeline Impact

| P# | Happy path | Failure |
|----|------------|---------|
| **P-WE ops recovery** | `/registry_health` OK → `/registry_export` → operator receives xlsx in TG | DB down → clear TG error; Dropbox outage **no longer blocks** export |
| Append/patch/refresh | Unchanged | Unchanged |

**Latency:** removes Dropbox download+upload from export — **faster**, fewer rev conflicts.

**Ops workflow change:** recovery no longer refreshes Dropbox workbook via export; operators use TG file. Background `schedule_excel_export` may still update Dropbox on commits.

---

## Integration Impact

| Интеграция | Phase 4 |
|------------|---------|
| **PostgreSQL** | Read-only export of results, runs, hold, otlezka, meta |
| **Dropbox** | **Not used** by `/registry_export`; still used by sync + background export |
| **Telegram** | `send_file_sync` / `send_document` with caption summary; larger payload (xlsx) |
| **Antares** | Unchanged |

**Telegram file size:** registry xlsx may be large — monitor TG limits (~50MB); no chunking in Phase 4.

---

## Cache / State Impact

| Артефакт | Change |
|----------|--------|
| Postgres tables | Read-only; no export-side writes |
| `excel_export_state` | Background export failures unchanged; TG export may use separate metrics or none |
| Temp files | Export xlsx in temp dir until TG send completes |

---

## Data Impact

| Данные | Phase 4 |
|--------|---------|
| PG tables | Source for all export sheets |
| Dropbox `wallet_editor.xlsx` | **Not updated** by `/registry_export` |
| Legacy generated sheets in Dropbox | Become further stale; **intentional** until Phase 5 |

---

## Regression Risks

| # | Risk | Prob | Mitigation |
|---|------|------|------------|
| 1 | Ops expects Dropbox refresh from export | **high** | runbook update; summary states «TG-only, Dropbox not updated» |
| 2 | Large xlsx TG send failure | med | clear error; log file size |
| 3 | hold/otlezka empty when manual sync never ran | med | warn in summary; export still useful for history |
| 4 | Accidental Dropbox calls left in export path | med | tests #3/#4; grep in review |
| 5 | `export_registry_workbook_to_dropbox` broken by refactor | med | keep function; separate new builder |
| 6 | Excel mode `WALLET_EDITOR_REGISTRY_SOURCE=excel` | low | explicit error «export requires postgres» |
| 7 | README/sync_status sheet format drift | low | unit test sheet names + key cells |

---

## Rollback Strategy

| Level | Action |
|-------|--------|
| Code | revert deploy — restores Dropbox export behavior |
| Config | n/a (no new env required) |
| Ops | use CLI `tools/export_wallet_editor_registry.py` for Dropbox rebuild |

---

## Вердикт impact

| Поле | Значение |
|------|----------|
| **Уровень риска** | **medium** |
| **Рекомендация** | **proceed with caution** |

**Условия proceed:**

1. Phases 1–3 deployed; PG holds manual data when sync enabled.
2. Ops informed: `/registry_export` no longer updates Dropbox.
3. Tests prove zero Dropbox IO in TG export path.
4. `schedule_excel_export` and append paths untouched.
5. Export remains read-only on DB.

**Benefits:** aligns export with PG SoT; removes Dropbox dependency for ops recovery; delivers file where operator invoked command; supports eventual Dropbox write deprecation (Phase 5).

**Costs:** ops habit change; E-WE-22 superseded; possible confusion while background Dropbox export still runs on commits.

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-07-01 | Cursor (draft) | Impact created for TASK-2026-07-01-04 |
