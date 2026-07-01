# WalletEditor Registry — PostgreSQL Cutover Ops Checklist

| Мета | Значение |
|------|----------|
| **Версия** | 1.0 |
| **Дата** | 2026-07-01 |
| **Программа** | WalletEditor Registry v2 (Phases 1–5 complete) |
| **Связанные ADR** | E-WE-23 … E-WE-27 |

Ручная ops-процедура. **Не выполняется кодом.** Выполняется оператором после деплоя Phase 5.

---

## Финальная архитектура

```text
Source of Truth
  PostgreSQL
    we_registry_results
    we_registry_runs
    manual_hold / manual_otlezka (after ManualSync)

Operator Input
  Dropbox workbook (DROPBOX_WALLET_EDITOR_PATH)
    hold
    Отлёжка

Reporting
  RegistryExportBuilder
    ├── /registry_export → Telegram
    └── CLI tools/export_wallet_editor_registry.py → local file
```

**Инварианты:**

- Runtime **никогда** не пишет `all_results` / `runs` в Dropbox.
- **ManualSync** — единственный компонент, который читает Dropbox workbook.
- Export workbook — **read-only**; редактирование запрещено.

---

## Перед деплоем

Проверить на Railway (или целевой среде):

| # | Проверка | Ожидаемое значение |
|---|----------|-------------------|
| 1 | Schema v2 применена | `we_registry_results`, `we_registry_runs`, manual tables существуют |
| 2 | `DATABASE_URL` | задан, приложение подключается |
| 3 | `WALLET_EDITOR_REGISTRY_SOURCE` | `postgres` (default; `excel` **не поддерживается**) |
| 4 | `WALLET_EDITOR_MANUAL_SYNC_ENABLED` | `1` |
| 5 | `WALLET_EDITOR_MANUAL_READERS_SOURCE` | `postgres` |
| 6 | `DROPBOX_WALLET_EDITOR_PATH` | задан (manual workbook path) |

**Rollback:** только Git revert / deploy предыдущей версии. Env `WALLET_EDITOR_REGISTRY_SOURCE=excel` **не** является rollback.

---

## Smoke (после деплоя, до cleanup workbook)

Выполнить в Telegram / через jobs:

| # | Проверка | Критерий успеха |
|---|----------|-----------------|
| 1 | Manual sync | `/manual_sync` или pre-run gate: `last_manual_sync_at` обновлён; health без `DEGRADED: no successful manual sync` |
| 2 | Disable | один disable-run → outbox `synced`; строки в PostgreSQL |
| 3 | Auto-enable | `/auto_enable_plan` + при необходимости `/auto_enable_run`; patch в PostgreSQL |
| 4 | Lifecycle | job `wallet_editor_registry_refresh` или `tools/refresh_registry_lifecycle_fields.py --apply` |
| 5 | `/registry_health` | `Registry projection: DISABLED (architecture)`; `Registry export: Telegram only`; нет projection warnings |
| 6 | `/registry_export` | Excel-документ в Telegram; данные из PostgreSQL; Dropbox **не** обновлён |

---

## Cleanup (manual workbook)

**Только после успешного smoke.** Выполняется оператором в Dropbox UI.

### 1. Backup

Скачать полную копию текущего `wallet_editor.xlsx` (все листы) в архив с датой:

```text
wallet_editor_backup_YYYYMMDD_pre_cutover.xlsx
```

### 2. Удалить generated sheets

Удалить листы (если присутствуют):

- `all_results`
- `runs`
- любые другие generated/history sheets (README export, sync_status и т.п. в **operator** workbook не должны существовать)

### 3. Оставить только operator sheets

Workbook должен содержать **только**:

| Лист | Назначение |
|------|------------|
| `hold` | operator input — card+partner block list |
| `Отлёжка` | operator input — partner cooling-off days |

---

## После cleanup

| # | Проверка | Критерий успеха |
|---|----------|-----------------|
| 1 | Manual sync | sync успешен; snapshot hash обновлён |
| 2 | Registry export | `/registry_export` — полный workbook из PG |
| 3 | Auto-enable | planning читает hold/Отлёжка из PG (после sync) |
| 4 | Hold enforcement | held card → SKIP на `add_partner` |

---

## Troubleshooting

| Симптом | Действие |
|---------|----------|
| `no successful manual sync` в health | выполнить manual sync; проверить `WALLET_EDITOR_MANUAL_SYNC_ENABLED=1` |
| Append blocked без Dropbox path | нормально при `MANUAL_READERS_SOURCE=postgres`; нужен только для manual sync ingest |
| Нужен полный registry Excel | `/registry_export` или CLI `tools/export_wallet_editor_registry.py --output <path>` |
| Откат после проблем | deploy предыдущего git commit; восстановить workbook из backup |

---

## История

| Дата | Событие |
|------|---------|
| 2026-07-01 | Checklist создан — TASK-2026-07-01-06 |
