# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-39 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-38 close `9221f052f8b9bacda10a3041757fa72f7687202c` (review `f12811035433bce0306ef9ef9d328db43652795a`, Draft PR #41); [MODULAR_REORG_ANTARES_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) |
| **PR** | Draft [#42](https://github.com/deniskotdavydov1991-wq/Test/pull/42) `feat/task-2026-09-17-39-antares-drain-stop`, base `feat/task-2026-09-17-38-antares-auto-enable-enqueue-impl` |
| **Риск** | high: общий executor, WE daemon, sender `run_forever`; mixed-stop не этот docs |

Контракт учёта принятой работы, drain и остановки isolated Antares. Runtime **не** менялся. Реализация **не** начата. TASK-38 повторно не закрывать. Merge/deploy нет.

---

## Goal

Зафиксировать зависимости Accepted work (executor / continuation / WE queue / active item / sender), критерии успешного drain и явного неуспешного shutdown — без выбора имён API и без runtime.

---

## Success Criteria

- [x] Цепочки executor, AE, ingest, schedules, PTB, sender/outbox обследованы на SHA TASK-38
- [x] seal vs continuation vs «не стопать worker пока оркестратор может put»
- [x] пустая Queue ≠ drain
- [x] порядок из зависимостей; timeout ≠ нет эффекта / не retry
- [x] already-dead / смерть до get — блокеры; матрица D1–D16; O1–O10 открыты
- [ ] GPT review
- [ ] реализация
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование исходников на review `f128110…` / close `9221f05…`; runtime не менялся; pytest не требовался |
| GPT | pytest **не** запускал (ожидается review контракта) |

---

## Out Of Scope

runtime; serve/live polling; mixed-stop; mixed gate; harness как production stop; надзор worker; merge/retarget/deploy; исходное Test; повторное закрытие TASK-38.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | docs-контракт isolated drain/stop; статус **review (подготовлено)** |
