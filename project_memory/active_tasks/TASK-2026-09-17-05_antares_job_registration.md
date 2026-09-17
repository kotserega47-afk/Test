# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-05 |
| **Статус** | in_progress |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-04 baseline (Draft PR #7), ADR E-MOD-01, `ops/MODULAR_REORG_MIGRATION.md` этап 3 |
| **PR** | Draft (этот PR) `feat/task-2026-09-17-05-antares-jobs`, base `feat/task-2026-09-17-04-behavior-baseline` |
| **Риск** | medium: владение регистрацией шести Antares job_type; handlers/runtime jobs не переносятся |

Первый ограниченный шаг этапа 3: вынести **регистрацию** Antares jobs из `integrations/tg_commands.py` в `modules/antares/jobs.py`. Mixed-поведение сохраняется. Isolated entry / `JOB_ACCEPT` / cutover **не** входят.

---

## Goal

Шесть ключей `wallet`, `hourly`, `rate`, `download`, `wallet_editor_registry_refresh`, `wallet_editor_registry_replay` регистрируются через явный `modules.antares.jobs.register_jobs(registry)`, вызываемый из прежней точки сборки `tg_commands.py`. Импорт `modules.antares.jobs` сам jobs не регистрирует и не тянет Telegram sender / браузер / БД / rules.

---

## Граница изменения

Переносится: две обёртки `run_hourly_job` / `run_download_job` и `registry.update` шести ключей.

Не переносится: handlers, аналитика, downloaders, WE engine, Raccoon, script_job:*.

Совместимые экспорты: `integrations.tg_commands.run_hourly_job` и `run_download_job` — те же объекты, без второй реализации.

Обращения, учтённые до правки: `tests/unit/test_telegram_routes_phase3a.py` (monkeypatch send path), `architecture_map` / contracts (документация). `core` не импортирует `modules.antares`.

---

## Success Criteria

- [x] `register_jobs(registry)` + без side effects на import модуля
- [x] Сборка по-прежнему в `tg_commands.py`; raccoon/script_job пути прежние
- [x] Эталон ключей/команд JSON не менялся; mixed registry совпадает
- [x] Hourly: skip / fail send без fingerprint; success send → state
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

Перенос аналитики/downloaders/WE; scheduler/gate; Railway; isolated Antares entry; merge PR #4/#5/#6/#7.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Старт: регистрация шести Antares keys в `modules/antares/jobs.py` |
