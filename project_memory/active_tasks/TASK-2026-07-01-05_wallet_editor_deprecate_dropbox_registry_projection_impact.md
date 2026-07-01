# Task Workflow v1 — Impact Analysis

| Мета | Значение |
|------|----------|
| **ID** | IMPACT-2026-07-01-05 |
| **Связанная задача** | TASK-2026-07-01-05 |
| **KB версия** | v1.8 |
| **Вердикт** | **proceed** |
| **Триггер** | Remove Dropbox registry projection from runtime; finalize PostgreSQL SoT |

---

## Runtime change summary

| Path | Before | After |
|------|--------|-------|
| Disable append (postgres) | PG + async Dropbox export | **PG only** |
| Auto-enable patch | PG + async Dropbox export | **PG only** |
| Lifecycle refresh | PG + async Dropbox export | **PG only** |
| CLI export | Dropbox upload | **local file via RegistryExportBuilder** |
| `/registry_export` | TG from PG | unchanged |
| Manual sync | Dropbox read → PG | unchanged |
| `registry_source=excel` | Full Dropbox write | **removed** (`LegacyRegistrySourceError`) |

---

## Contracts impact

| Контракт | Change | Breaking |
|----------|--------|----------|
| E-WE-21 Excel projection | **Removed from runtime** | yes for ops relying on live Dropbox `all_results` |
| `DROPBOX_WALLET_EDITOR_PATH` | Manual sheets only (steady state) | behavioral |
| Outbox semantics | unchanged | no |
| I-MAN-08 manual ingest | unchanged | no |

**Rollback:** Git/deploy only. `WALLET_EDITOR_REGISTRY_SOURCE=excel` is rejected at runtime.

---

## Regression risks

| # | Risk | Mitigation |
|---|------|------------|
| 1 | Ops read registry from Dropbox | `/registry_export`; ops Task 06 |
| 2 | Append without Dropbox path | Allowed when `MANUAL_READERS_SOURCE=postgres` |
| 3 | External tools using old CLI upload | CLI now local-only; documented |

---

## История

| Дата | Событие |
|------|---------|
| 2026-07-01 | draft — projection flag proposed |
| 2026-07-01 | **proceed** — final decision: full removal, no flags |
