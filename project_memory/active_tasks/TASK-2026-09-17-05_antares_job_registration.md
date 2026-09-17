# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-05 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-04 baseline (Draft PR #7), ADR E-MOD-01, `ops/MODULAR_REORG_MIGRATION.md` этап 3 |
| **PR** | Draft [#8](https://github.com/deniskotdavydov1991-wq/Test/pull/8) `feat/task-2026-09-17-05-antares-jobs`, base `feat/task-2026-09-17-04-behavior-baseline` |
| **HEAD (проверен GPT)** | `a6d7ebcf6e1f3e2c12fb9a5b5cd8ba69062c513f` |
| **Риск** | medium: владение регистрацией шести Antares job_type; handlers/runtime jobs не переносятся |

Регистрация шести Antares jobs **выделена**, review пройден. Legacy mixed сохраняется. Isolated entry и cutover **не** реализованы. PR #8 остаётся Draft.

---

## Goal

Шесть ключей `wallet`, `hourly`, `rate`, `download`, `wallet_editor_registry_refresh`, `wallet_editor_registry_replay` регистрируются через явный `modules.antares.jobs.register_jobs(registry)`, вызываемый из прежней точки сборки `tg_commands.py`. Импорт `modules.antares.jobs` сам jobs не регистрирует и не тянет Telegram sender / браузер / БД / rules.

---

## Граница изменения

Перенесено: две обёртки `run_hourly_job` / `run_download_job` и `registry.update` шести ключей.

Не перенесено: handlers, аналитика, downloaders, WE engine, Raccoon, script_job:*.

Совместимые экспорты: `integrations.tg_commands.run_hourly_job` и `run_download_job` — те же объекты, без второй реализации.

`core` не импортирует `modules.antares`.

---

## Success Criteria

- [x] `register_jobs(registry)` + без side effects на import модуля
- [x] Сборка по-прежнему в `tg_commands.py`; raccoon/script_job пути прежние
- [x] Эталон ключей/команд JSON не менялся; mixed registry совпадает
- [x] Hourly: skip / fail send / exception send без fingerprint; success send → state (порядок send, затем state)
- [x] GPT review HEAD `a6d7ebcf…`: оба замечания закрыты, блокирующих нет
- [ ] merge/deploy PR #8 (намеренно открыто)

---

## Ограничения покрытия (TASK-04, без изменений)

Полный перенос модулей **не** готов. По-прежнему без воспроизводимого эталона:

- wallet file→DTO;
- отдельный payout-report;
- conversion Excel cell-level;
- raccoon daily conversion full text;
- Platform wallet-analyzer.

Регистрация jobs **не** доказывает включение production schedules.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **78 passed** на HEAD `6db95d76…` (связанный набор inventory/registration/antares jobs/parser+gate/hourly goldens/routes). **Не** повторялся на `a6d7ebcf…`. |
| Cursor | **9 passed** на HEAD `a6d7ebcf…`, Python **3.12.10** (`tests/test_antares_jobs.py`, `tests/test_behavior_baseline_registration.py`) |
| GPT | Проверил код и diff HEAD `a6d7ebcf…`. Наборы 78 и 9 **независимо не запускал.** |

---

## Out Of Scope

Перенос аналитики/downloaders/WE; scheduler/gate; Railway; isolated Antares entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4/#5/#6/#7/#8.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Старт: регистрация шести Antares keys в `modules/antares/jobs.py` |
| 2026-09-17 | Harness: различимые stub-исполнители; hourly порядок send→state |
| 2026-09-17 | GPT review HEAD `a6d7ebcf…`: блокирующих нет; merge/deploy нет |
