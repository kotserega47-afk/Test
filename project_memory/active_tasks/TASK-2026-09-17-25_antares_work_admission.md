# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-25 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-24 (PR #27, review `34ef7af33cc112d369e0c9ee860d66954268ce8c`, закрытие `364976424b1818e37a76bc0b1d10cb254e4479de`), `ops/MODULAR_REORG_ANTARES_WORK_ADMISSION.md` |
| **PR** | Draft [#28](https://github.com/deniskotdavydov1991-wq/Test/pull/28) `feat/task-2026-09-17-25-antares-work-admission`, base `feat/task-2026-09-17-24-antares-ptb-lifecycle` |
| **Риск** | low: только документы |

Контракт допуска **к review, не закрыт**. Уточнены состояния unbound vs bound/closed, атомарный submit, узкий первый code scope. Runtime **не** менялся. Pytest **не** запускался. Допуск **не** реализован.

---

## Goal

Зафиксировать владельца допуска, карту реальных путей (не только `JOB_REGISTRY`), точку принятия при гонке с enqueue, ответы при sealed, сохранение mixed, один будущий code scope с Event/barrier.

---

## Success Criteria

- [x] Карта: TG update → callback; dispatch → `request_job`; прямые registry/Auto-Enable; ingest → worker; будущие schedules и внутренний re-enqueue
- [x] Допуск не сведён к `JOB_REGISTRY`
- [x] Четыре состояния: unbound / bound/closed / open / sealed; isolated bind до initialize; ошибка start ≠ unbound
- [x] Единица принятия: `executor.submit` под коротким lock; не lock через await/reply/весь job; не обёртка вокруг `dispatch_job_async`
- [x] «Запускаю» после успешного submit на первом пути (явная смена порядка)
- [x] Первый code: примитив + только `/run_wallet`; прочие пути — явные обходы
- [x] Внутренний Auto-Enable/job re-enqueue — следующий этап, не глобальный запрет
- [x] `request_antares_stop`: `seal()` затем `stop.set()` в loop-thread; другой поток — `seal` + `call_soon_threadsafe`
- [x] Accepted: сбой reply не отменяет submit; сбой submit не Accepted
- [x] Узкий первый code: примитив + `/run_wallet`; переход Antares — отдельный план в MIGRATION.md, не этот code
- [ ] реализация допуска (отдельное задание; **не** начинать, пока review)
- [ ] закрытие TASK-25 (намеренно открыто)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Обследование исходников на закрытии TASK-24 `3649764…`; pytest не требовался |
| GPT | ещё не ревьюил |

---

## Out Of Scope

runtime/тесты TASK-25; live polling; sender stop; worker join; executor shutdown; mixed gate; `JOB_ACCEPT` env; реализация перехода; merge; исходное Test. План перехода — docs в MIGRATION, не code.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | контракт допуска подготовлен к review; Draft PR #28 |
| 2026-09-21 | уточнены состояния bind/closed, атомарный submit, stop vs seal, узкий `/run_wallet` scope |
| 2026-09-21 | MIGRATION: статусы Draft/sandbox/выпуск; план перехода A–F; оценка диапазонами; Accepted/reply/submit; loop vs `call_soon_threadsafe`; TASK-25 не закрыт |
| 2026-09-21 | review плана: TASK-04 прогоны 94@493c776 + 1+1@443ba70; оценка по категориям вместо 15–35; остановка mixed ≠ isolated seal; репозиторий по-прежнему Test |
