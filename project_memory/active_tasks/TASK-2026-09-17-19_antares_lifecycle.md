# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-19 |
| **Статус** | review (ожидает GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-18 (PR #21, review `2c6eeac34940f70413be70da35ddbc81e720ec83`, закрытие `d1d11e308c1abc9b0a5ee4531c3249559b16c5a0`, тесты `0603eb9ac42c3c04b282df6b38ed804b62db7307`), `ops/MODULAR_REORG_ANTARES_LIFECYCLE.md`, `ops/MODULAR_REORG_ANTARES_ENTRYPOINT.md` § 4 |
| **PR** | Draft [#22](https://github.com/deniskotdavydov1991-wq/Test/pull/22) `feat/task-2026-09-17-19-antares-lifecycle`, base `feat/task-2026-09-17-18-antares-boot` |
| **Риск** | low: только документы |

Контракт запуска и остановки isolated Antares **подготовлен к review**. Runtime, mixed gate, Railway и профили **не** менялись. `python -m apps.antares` остаётся boot exit 0. Polling, worker, schedules, sender loop, `JOB_ACCEPT` и cutover **не** реализованы.

Опора: принятый план entrypoint TASK-17 и фактический boot TASK-18.

---

## Goal

Зафиксировать выполнимый lifecycle: сохранить boot, отдельно включить `run`, fail-fast snapshot до Application, фильтр семи job keys до dispatch, владение ресурсами без ложного graceful shutdown, изоляцию token/files vs process-local, минимальный следующий code PR.

---

## Success Criteria

- [x] Boot без argv = TASK-18 (сборка, exit 0, нет polling)
- [x] `run` — отдельный argv; запрет после неуспешной сборки
- [x] Порядок: snapshot до Application (не копировать mixed)
- [x] Таблица start/stop: sender, Application, executor, schedule thread, worker queues — что есть / чего нет
- [x] Фильтр семи Antares keys до `dispatch_job_background`; unknown workbook job_key без dispatch
- [x] Изоляция: token, workbook/routes, STATE_DIR/locks, tmp auth-state, WE очереди; нет mixed/raccoon bootstrap; семь keys ≠ изоляция сервиса
- [x] Первый code scope: argv + fail-fast snapshot, без Application/polling
- [x] Subprocess-проверки описаны; pytest в TASK-19 **не** требуется
- [ ] GPT review этого плана
- [ ] merge/deploy (намеренно открыто)
- [ ] code PR argv+snapshot (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы по исходникам на `d1d11e3…`; pytest **не** требовался |
| GPT | ещё не проверял |

---

## Out Of Scope

runtime/тесты TASK-19; реализация `run` в этом PR; polling; worker execution; schedules; sender shutdown; `JOB_ACCEPT`; Railway; cutover; merge/retarget/deploy; исходное дерево Test; живой workbook/prod env.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | контракт lifecycle подготовлен к review; Draft PR #22 |
