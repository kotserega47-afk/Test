# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-18 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-17 (PR #20, review `ed3cbaf2b240786c7985108dac9a7cd775f2b871`, закрытие pin `e3bbac8338c6a074f0fa9aef871c7b92f0cb5a2c`) |
| **PR** | Draft [#21](https://github.com/deniskotdavydov1991-wq/Test/pull/21) `feat/task-2026-09-17-18-antares-boot`, base `feat/task-2026-09-17-17-antares-entrypoint` |
| **HEAD (принятый review)** | `2c6eeac34940f70413be70da35ddbc81e720ec83` |
| **HEAD (проверенные тесты)** | `0603eb9ac42c3c04b282df6b38ed804b62db7307` |
| **Риск** | medium: isolated boot + lazy sender imports |

Isolated `python -m apps.antares`: gate → `{repo_root}/.env` (`override=False`) → token strip → AccessRules/logger → `assemble_antares` → диагностика → **exit 0**. Процесс завершается. Sender loop, polling, worker и schedules **не** запускаются. Mixed gate **не** менялся. Review **пройден** на принятом HEAD `2c6eeac…`. Сборка **не выпущена**. PR #21 остаётся Draft. Следующий этап — TASK-19 (контракт lifecycle, runtime не менять).

---

## Goal

Собрать Antares в отдельном процессе без polling и без import-time sender loop.

---

## Граница изменения

Добавлено: `apps/__init__.py`, `apps/antares.py`; isolated gate `enforce_antares_isolated_profile`; boot subprocess harness.

Изменено: lazy-import `telegram_bot` в `telegram_transport`, bakai, registry, registry_refresh.

Review-fix (тесты/harness, production не менялся): конфликт boot после полной загрузки модуля; send-тесты в child-harness. Подмена `_append_attempt` на тестовый Dropbox-алгоритм **удалена**.

Не менялись: `scheduler.py`, `enforce_legacy_scheduler_profile`, Railway, `JOB_ACCEPT`.

---

## Success Criteria

- [x] `python -m apps.antares` из корня; без правки `sys.path`
- [x] Explicit `antares` only; отсутствие ключа в процессе → exit 2
- [x] Lazy sender; harness запрещает загрузку telegram_bot/mixed/raccoon
- [x] Успех: 17 commands + document, семь jobs, exit 0; процесс не остаётся живым
- [x] Review-fix: отказ boot по foreign keys / different AccessRules; send isolation
- [x] Убрана тестовая реализация Dropbox append; карта покрытия ниже
- [x] GPT review принятого HEAD `2c6eeac…`: код/diff; набор тестов **не** запускал; блокирующих нет
- [ ] merge/deploy PR #21 (намеренно открыто)
- [ ] TASK-19 lifecycle contract (отдельное задание)

---

## Прогоны (Cursor, Python 3.12.10)

Исторический результат Cursor на `54ffa35…` (часть registry-тестов шла через замену `_append_attempt` на `dropbox_append_attempt`; **не** regression текущего postgres append; ограничение сохраняется):

```
python -m pytest tests/unit/test_antares_boot.py tests/unit/test_telegram_transport.py tests/unit/test_wallet_editor_dropbox_registry.py tests/unit/test_wallet_editor_registry_timeout.py tests/unit/test_wallet_editor_registry_refresh.py -q --tb=short
```

**85 passed, 0 failed, 0 skipped, exit 0.**

После удаления тестового append (`0603eb9ac42c3c04b282df6b38ed804b62db7307`, тот же набор файлов, Python **3.12.10**, прогон Cursor):

**79 passed, 0 failed, 0 skipped, exit 0.**

Ожидаемые отказы boot (без изменений harness injection):

- `JOB_REGISTRY has foreign keys: ['raccoon_hourly']`
- `antares handlers already bound to a different AccessRules instance`

### Карта покрытия (после удаления dropbox_append_attempt)

Реальные функции: isolated boot/`assemble_antares`; `send_text`/`send_document`; `_send_to_current_route`; `_send_chat_warning`; `_send_to_route`; `_process_missing_otlezka_warnings`; lifecycle/xlsx (`recalculate_all_results`, `rows_from_result_excel`, `normalize_all_results`, `save_registry_workbook`, `load_registry_frames`, `run_id_already_processed`); `append_run_to_dropbox_registry` orchestration (retry/timeout); `append_attempt_postgres`; `refresh_attempt_postgres` / `refresh_wallet_editor_registry_lifecycle`; settings/async staging/worker send-order.

Подменённые границы: sender stub только в child-harness; `connection.connect` / `_get_dbx`; postgres load/persist/store; `_append_attempt` как узкая последовательность TRANSIENT/SUCCESS без workbook; `_send_to_route` в refresh (отправка refresh покрыта send-boundary).

Не подтверждают текущий runtime: Dropbox rev-CAS upload как путь append (`upload_file_if_rev` внутри `_append_attempt`); исторический 85 passed на тестовом Dropbox-алгоритме.

GPT проверил код/diff принятого HEAD, тесты не запускал.

Production registry и mixed gate не менялись. Изменения **не выпущены**.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | 85 passed (с заменой append, ограничение сохраняется); затем 79 passed без тестового Dropbox-алгоритма, Python **3.12.10** |
| GPT | смотрел код/diff принятого HEAD `2c6eeac…`; тесты не запускал |

polling; worker; schedules; idle; JOB_ACCEPT; Railway; cutover; merge; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | isolated Antares boot без polling |
| 2026-09-20 | review-fix: conflict injection after load, send child-harness, dropbox fixtures; GPT новый diff ещё не проверял |
| 2026-09-20 | убрана тестовая реализация registry append; GPT смотрел код/diff, тесты не запускал |
| 2026-09-20 | review пройден на HEAD `2c6eeac…`; 79 passed на `0603eb9…`; PR #21 Draft, не выпущен; следующий — TASK-19 |
