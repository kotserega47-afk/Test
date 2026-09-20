# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-18 |
| **Статус** | review (ожидает GPT на новый diff; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-17 (PR #20, review `ed3cbaf2b240786c7985108dac9a7cd775f2b871`, закрытие pin `e3bbac8338c6a074f0fa9aef871c7b92f0cb5a2c`) |
| **PR** | Draft [#21](https://github.com/deniskotdavydov1991-wq/Test/pull/21) `feat/task-2026-09-17-18-antares-boot`, base `feat/task-2026-09-17-17-antares-entrypoint` |
| **HEAD (проверенные тесты)** | `54ffa35da381b286bcb31ce57f9e4a7bc519955f` |
| **Риск** | medium: isolated boot + lazy sender imports |

Isolated `python -m apps.antares`: gate → `{repo_root}/.env` (`override=False`) → token strip → AccessRules/logger → `assemble_antares` → диагностика → exit 0. Polling/worker/schedules **нет**. Mixed gate **не** менялся.

---

## Goal

Собрать Antares в отдельном процессе без polling и без import-time sender loop.

---

## Граница изменения

Добавлено: `apps/__init__.py`, `apps/antares.py`; isolated gate `enforce_antares_isolated_profile`; boot subprocess harness.

Изменено: lazy-import `telegram_bot` в `telegram_transport`, bakai, registry, registry_refresh.

Review-fix (тесты/harness, production не менялся): конфликт boot вносится один раз после полной загрузки модуля, без `__import__` hook и без доступа к частичным модулям; send-тесты в child-harness со stub до импорта; Dropbox/timeout fixtures с явным dropbox backend и блоком PG.

Не менялись: `scheduler.py`, `enforce_legacy_scheduler_profile`, Railway, `JOB_ACCEPT`.

---

## Success Criteria

- [x] `python -m apps.antares` из корня; без правки `sys.path`
- [x] Explicit `antares` only; отсутствие ключа в процессе → exit 2
- [x] Lazy sender; harness запрещает загрузку telegram_bot/mixed/raccoon
- [x] Успех: 17 commands + document, семь jobs, exit 0
- [x] Review-fix: отказ boot по foreign keys / different AccessRules; send isolation; registry fixtures
- [ ] GPT review нового diff (ещё не проверял)
- [ ] merge/deploy

---

## Прогоны (Cursor, Python 3.12.10)

Review-fix (проверенный код тестов `54ffa35da381b286bcb31ce57f9e4a7bc519955f`):

```
python -m pytest tests/unit/test_antares_boot.py tests/unit/test_telegram_transport.py tests/unit/test_wallet_editor_dropbox_registry.py tests/unit/test_wallet_editor_registry_timeout.py tests/unit/test_wallet_editor_registry_refresh.py -q --tb=short
```

**85 passed, 0 failed, 0 skipped, exit 0.** Интерпретатор: `C:\Users\sereg\AppData\Local\Programs\Python\Python312\python.exe` (Python **3.12.10**). Прогон Cursor.

Ожидаемые отказы boot (оба rc=1, без `antares boot ok`, без forbidden import / AttributeError / RecursionError):

- загрязнение registry: `JOB_REGISTRY has foreign keys: ['raccoon_hourly']`
- несовместимый bind: `antares handlers already bound to a different AccessRules instance`

Production после review-fix не менялся; assembly/mixed regression не повторялись.

Ранее, до review-fix: boot+send 23 passed; assembly 8 passed в составе 168 passed (parser/gate/mixed/handlers/jobs/routes/refresh).

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | прогоны выше, включая review-fix 85 passed |
| GPT | новый diff review-fix ещё не проверял |

polling; worker; schedules; idle; JOB_ACCEPT; Railway; cutover; merge; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | isolated Antares boot без polling |
| 2026-09-20 | review-fix: conflict injection after load, send child-harness, dropbox fixtures; GPT новый diff ещё не проверял |
