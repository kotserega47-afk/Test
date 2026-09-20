# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-16 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-15 (PR #18, закрытие `89873a0749708460579311b452613b1167e3df43`, pin `14e36a7ca86d0ee8ba591cb9e6a2aa9530739e17`), `ops/MODULAR_REORG_ANTARES_ASSEMBLY.md` § подэтап 2 |
| **PR** | Draft [#19](https://github.com/deniskotdavydov1991-wq/Test/pull/19) `feat/task-2026-09-17-16-antares-assembly`, base `feat/task-2026-09-17-15-script-job-bind` |
| **HEAD (проверен GPT)** | `f29cc8918eef022e0a4b88b1ac7f59a67212798f` |
| **Закрытие docs** | (этот коммит на ветке PR #19) |
| **Риск** | medium: isolated сборка handlers/jobs без запуска |

Подэтап 2 плана TASK-14: явная `assemble_antares`, новые Antares start/help/status, перенос whoami/reload_rules/rules_validate с identity re-export в mixed. Семь jobs на фактическом `JOB_REGISTRY`. Mixed start/help/status, `KNOWN_JOB_TYPES` и `expected_tg_commands.json` не менялись. Isolated entrypoint, polling, worker, schedules, `JOB_ACCEPT` и cutover **не** реализованы. Сборка Antares **реализована, не выпущена**. Review **пройден** на HEAD `f29cc89…`. PR #19 остаётся Draft.

---

## Goal

Собрать isolated Antares handlers и jobs без запуска процесса и без импорта mixed `tg_commands` / raccoon-модулей.

---

## Граница изменения

Добавлено: `modules.antares.assembly`; Antares `cmd_start`/`cmd_help`/`cmd_status`; `get_antares_handlers`; эталон `expected_antares_tg_commands.json`; child-harness сборки.

Изменено: перенос whoami/reload_rules/rules_validate в `handlers.py`; mixed re-export тех же объектов; `register_jobs` через `antares_job_executors()`; `core.job_health` принимает явный `job_types` для расчёта Antares (семь keys), mixed вызов без параметра и `KNOWN_JOB_TYPES` сохранены; кеш mixed/Antares разделён.

Не менялись: `scheduler.py`, `project_profile_boot.py`, mixed start/help/status, `KNOWN_JOB_TYPES`, mixed expected JSON, профили, Railway, durable inbox.

---

## Success Criteria

- [x] 17 CommandHandler + Document.ALL owner callback, без raccoon/hello
- [x] Ровно семь keys на `core.job_runner.JOB_REGISTRY`; отказ при чужих ключах / конфликте / чужом mapping / другом bind без мутации
- [x] Повтор с теми же rules/logger/executors допустим
- [x] Сбой импорта — неуспех, частичный registry не считать сборкой
- [x] Antares help/status по §6; mixed `format_job_health_lines()` as-is не используется; расчёт job health для семи keys через `job_types=`
- [x] GPT review HEAD `f29cc89…`: блокирующих нет
- [ ] merge/deploy PR #19 (намеренно открыто)
- [ ] isolated entrypoint / TASK-17 (отдельное задание)

---

## Прогоны (Cursor, Python 3.12.10, worktree TASK-16, Start-Process, HasExited=True)

1. Isolated assembly (child harness; **8 parent wrappers**, внутренние кейсы в commands: help/status/whoami + observation ON + rules_validate): `py -3.12 -m pytest tests/unit/test_antares_assembly.py -q --tb=short` → **8 passed**, 0 failed, 0 skipped, **EXIT=0**
2. Job health: `py -3.12 -m pytest tests/unit/test_job_health_c1.py -q --tb=short` → **11 passed**, EXIT=0
3. Mixed baseline + parser/gate отдельно: `py -3.12 -m pytest tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/test_raccoon_tg_commands.py tests/test_scheduler_clocks_reset.py tests/test_antares_handlers.py tests/test_antares_jobs.py tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py -q --tb=line` → **102 passed**, EXIT=0

Слои: AST/inventory — статика; child assemble — реальные `JOB_REGISTRY` / `register_jobs` / `register_script_job` / callbacks; sitecustomize ребёнка — только telegram_bot и playwright.sync_api, не raccoon/tg_commands.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | прогоны выше (Python 3.12.10) |
| GPT | Проверил код/diff HEAD `f29cc89…`. Тесты **не** запускал. |

---

## Out Of Scope

entrypoint; polling; worker execution; schedules; `JOB_ACCEPT`; split `job_runner`; Railway; merge/retarget/deploy.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-19 | сборка Antares без запуска; статус review |
| 2026-09-19 | PR #19; параметризован расчёт job health на семь keys |
| 2026-09-20 | GPT review HEAD `f29cc89…`: блокирующих нет; merge/deploy нет; PR #19 Draft |
| 2026-09-20 | документационное закрытие на ветке PR #19; сборка реализована, не выпущена |
