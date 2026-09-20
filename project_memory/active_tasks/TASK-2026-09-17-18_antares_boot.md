# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-18 |
| **Статус** | review (ожидает GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-17 (PR #20, review `ed3cbaf2b240786c7985108dac9a7cd775f2b871`, закрытие pin `e3bbac8338c6a074f0fa9aef871c7b92f0cb5a2c`) |
| **PR** | Draft (этот PR) `feat/task-2026-09-17-18-antares-boot`, base `feat/task-2026-09-17-17-antares-entrypoint` |
| **Риск** | medium: isolated boot + lazy sender imports |

Isolated `python -m apps.antares`: gate → `{repo_root}/.env` (`override=False`) → token strip → AccessRules/logger → `assemble_antares` → диагностика → exit 0. Polling/worker/schedules **нет**. Mixed gate **не** менялся.

---

## Goal

Собрать Antares в отдельном процессе без polling и без import-time sender loop.

---

## Граница изменения

Добавлено: `apps/__init__.py`, `apps/antares.py`; isolated gate `enforce_antares_isolated_profile`; boot subprocess harness.

Изменено: lazy-import `telegram_bot` в `telegram_transport`, bakai, registry, registry_refresh; patch-пути тестов отправки на `integrations.telegram_bot.*`.

Не менялись: `scheduler.py`, `enforce_legacy_scheduler_profile`, Railway, `JOB_ACCEPT`.

---

## Success Criteria

- [x] `python -m apps.antares` из корня; без правки `sys.path`
- [x] Explicit `antares` only; отсутствие ключа в процессе → exit 2
- [x] Lazy sender; harness запрещает загрузку telegram_bot/mixed/raccoon
- [x] Успех: 17 commands + document, семь jobs, exit 0
- [ ] GPT review
- [ ] merge/deploy

---

## Прогоны (Cursor, Python 3.12.10)

1. Boot + sender boundary: `py -3.12 -m pytest tests/unit/test_antares_boot.py tests/unit/test_telegram_transport.py -q --tb=short` → **23 passed**, EXIT=0. Реальный `-m apps.antares` в копии дерева; telegram_bot/mixed/raccoon **не** stub в `sys.modules`.
2. Assembly: `tests/unit/test_antares_assembly.py` — **8 passed** (в общем прогоне ниже).
3. Parser/gate + mixed baseline + handlers/jobs + bakai/wallet send + refresh: `tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/test_raccoon_tg_commands.py tests/test_scheduler_clocks_reset.py tests/test_antares_handlers.py tests/test_antares_jobs.py tests/unit/test_telegram_routes_phase3b.py tests/unit/test_telegram_routes_phase3d.py tests/unit/test_wallet_editor_registry_refresh.py` вместе с assembly → **168 passed**, EXIT=0 (включая 8 assembly).

Dropbox/timeout registry suites **не** гонялись до конца: без `load_dotenv` рабочего `.env` нет `DATABASE_URL`; подставлять живой URL нельзя (зависание connect). Границы send для registry/refresh/bakai/transport покрыты `test_telegram_transport.py`.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | прогоны выше |
| GPT | ещё не проверял этот diff |

polling; worker; schedules; idle; JOB_ACCEPT; Railway; cutover; merge; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | isolated Antares boot без polling |
