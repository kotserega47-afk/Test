# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-25 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-24 (PR #27, review `34ef7af33cc112d369e0c9ee860d66954268ce8c`, закрытие `364976424b1818e37a76bc0b1d10cb254e4479de`), `ops/MODULAR_REORG_ANTARES_WORK_ADMISSION.md` |
| **PR** | Draft [#28](https://github.com/deniskotdavydov1991-wq/Test/pull/28) `feat/task-2026-09-17-25-antares-work-admission`, base `feat/task-2026-09-17-24-antares-ptb-lifecycle` |
| **Риск** | low: только документы |

Контракт допуска новой работы isolated Antares **подготовлен к review**. Runtime **не** менялся. Pytest **не** запускался. Допуск **не** реализован. PTB lifecycle TASK-24 **не выпущен**.

---

## Goal

Зафиксировать владельца допуска, карту реальных путей (не только `JOB_REGISTRY`), точку принятия при гонке с enqueue, ответы при sealed, сохранение mixed, один будущий code scope с Event/barrier.

---

## Success Criteria

- [x] Карта: TG update → callback; dispatch → `request_job`; прямые registry/Auto-Enable; ingest → worker; будущие schedules и внутренний re-enqueue
- [x] Допуск не сведён к `JOB_REGISTRY`
- [x] Владелец: isolated process; unbound default; open после start; seal навсегда на этот запуск
- [x] Слои: новые updates / начало callback / постановка / уже принятая работа
- [x] `try_accept` + enqueue под lock; несинхронизированный bool недостаточен
- [x] Closed replies ≠ ACL и ≠ busy/unknown job
- [x] Нет обещания cancel running jobs
- [x] Mixed unbound без изменения поведения
- [x] Нет production `JOB_ACCEPT` env в этом контракте
- [x] Один будущий code этап; polling/sender/worker join/executor shutdown — отдельные зависимости
- [ ] реализация допуска (отдельное задание)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Обследование исходников на закрытии TASK-24 `3649764…`; pytest не требовался |
| GPT | ещё не ревьюил |

---

## Out Of Scope

runtime/тесты TASK-25; live polling; sender stop; worker join; executor shutdown; mixed gate; `JOB_ACCEPT` env; Railway; cutover; merge; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | контракт допуска подготовлен к review; Draft PR #28 |
