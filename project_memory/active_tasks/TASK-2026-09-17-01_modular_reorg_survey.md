# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-01 |
| **Статус** | in_progress |
| **KB версия** | v1.10 |
| **Связанные артефакты** | `TASK-2026-09-17-01_modular_reorg_survey_impact.md`, `CP-dev_task-TASK-2026-09-17-01-20260917.md`, `ops/MODULAR_REORG_SURVEY.md`, `ops/MODULAR_REORG_ADR.md`, `ops/MODULAR_REORG_MIGRATION.md` |
| **Риск** | docs; merge в `test_main` может рестартовать Test (автодеплой) |

---

## Goal

Зафиксировать фактическое состояние Test и Platform_2.0, спроектировать модульные границы Antares / Raccoon / WR и поэтапную миграцию **без** изменения рабочего кода, Railway sources, БД и расписаний.

---

## Business Context

Потребители: ops Antares (отчёты, Wallet Editor), ops Raccoon (PayIn/hourly), будущий WR. Смешивание репозиториев и одноимённых модулей уже создаёт риск двойного запуска Raccoon. Программа объединения должна сохранить сервисы, Telegram, отчёты и WE.

Не смешивать с проектом `telegram_task_registry`.

---

## Current Behavior

Ссылки KB: P1–P4, P-WE; `current_state.md` R1–R6 (Test).  
Уточнение 2026-09-17: Test **код** содержит Raccoon jobs (SHA задеплоен F25); **исполнение** этих jobs в prod Test = U12. Platform `develop` задеплоен как Raccoon (F26). PR #2 Draft/OPEN `7aadf49`. Live job health — UNKNOWN.

---

## Desired Behavior

В репозитории Test: survey + ADR + migration + конкретный следующий code Task. Draft PR только с документацией. Незакоммиченные локальные файлы исходного клона не входят в PR.

---

## Affected Pipelines / Modules

Документация: `project_memory/ops/*`, `active_tasks/*`, указатели KB.  
Runtime `.py`: **не** в diff. Merge в `test_main` всё равно может перезапустить сервис Test.

---

## Success Criteria

- [x] Обследование двух репозиториев с SHA
- [x] Таблица функций и blob-diff одноимённых файлов
- [x] ADR изоляции и границ модулей
- [x] Этапы PR, cutover, rollback, QA
- [x] TASK-2026-09-17-02 сужен (парсер only); TASK-2026-09-17-03 — early gate
- [x] Draft PR #4 https://github.com/deniskotdavydov1991-wq/Test/pull/4

---

## Out Of Scope

Рабочий код, dependencies, production config, DB schema, Railway connected sources, merge/deploy, реальные операции с кошельками и рассылка отчётов, WR implementation, telegram_task_registry.

---

## Known Risks

| Риск | Уровень | Комментарий |
|------|---------|-------------|
| Неверные live SHA | med | частично закрыто F25/F26; health остаётся U1 |
| Dual Raccoon | high | U2/U10/U12; не закрывается SUCCESS деплоя |
| Автодеплой docs | med | merge `test_main` может рестартовать Test |
| Platform develop tracked `.env` | high (security) | отдельный PR на Platform; секреты не копировать |

---

## Constraints

- Секреты не в git/docs
- Worktree от `origin/test_main`, не мешать грязный checkout
- Не объявлять изоляцию из `project_id` в логах
- Не считать WR=Antares по UI

---

## Reading Order

1. `project_memory/workflow.md`
2. `project_memory/current_state.md`
3. `project_memory/architecture_map.md`
4. `project_memory/ops/MODULAR_REORG_SURVEY.md`
5. `project_memory/ops/MODULAR_REORG_ADR.md`
6. `project_memory/ops/MODULAR_REORG_MIGRATION.md`

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Обследование; docs в worktree `docs/modular-reorg-antares-raccoon-wr` |
| 2026-09-17 | Review PR #4: автодеплой, cutover, locks, Railway SHA, сужение TASK-02, TASK-03 |
