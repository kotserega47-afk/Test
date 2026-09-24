# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-39 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-38 close `9221f052f8b9bacda10a3041757fa72f7687202c` (review `f12811035433bce0306ef9ef9d328db43652795a`, Draft PR #41); [MODULAR_REORG_ANTARES_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) |
| **PR** | Draft [#42](https://github.com/deniskotdavydov1991-wq/Test/pull/42) `feat/task-2026-09-17-39-antares-drain-stop`, base `feat/task-2026-09-17-38-antares-auto-enable-enqueue-impl` |
| **Риск** | high: общий executor, WE daemon, sender `run_forever`; mixed-stop не этот docs |

Review **пройден**. GPT проверил документ на полном SHA `69ae53c65b66a9ad918c294d3f6f1e53192c47a0`. GPT pytest **не** запускал. Runtime **не** менялся. Этот docs-коммит — закрытие TASK-39. Контракт isolated drain/stop **принят**, **не выпущен**. Реализация — отдельные срезы, первый TASK-40 (реестр Accepted executor Futures). TASK-38 повторно не закрывать. PR #42 остаётся Draft. Merge/deploy нет.

---

## Goal

Полный учёт isolated Accepted executor Futures на `WorkAdmission`; work drain отдельно от resource shutdown; sender в полном graceful; один порядок seal → stop.set → drain → PTB/sender/WE/executor.

---

## Success Criteria

- [x] Реестр Futures на admission, согласован с submit/seal; callback вне lock; cancel handler не снимает работу
- [x] Sender S1–S3 + rollback handoff; intake seal вместе с idle; `send_photo_sync` вне очереди
- [x] Work drain vs resource shutdown; финальный WE список после producers+executor+continuation
- [x] Registry daemon: учёт/join для полного graceful до sender; `delayed_cleanup` — отдельное исключение
- [x] Cancel drain не трогает Accepted Future (не голый wrap_future); один cleanup
- [x] Helper порядок: drain → registry join → WE sentinel → sender → PTB
- [x] Dead worker/deadline = failure + отчёт, без retry (в т.ч. живой registry daemon)
- [x] Матрица D1–D30; решения O1–O9 выбраны; O10 mixed-stop до cutover
- [x] GPT review документа на `69ae53c…`; pytest GPT не запускал
- [ ] реализация (TASK-40+; не этот PR)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | контракт и уточнения; runtime нет; pytest не требовался |
| GPT | review документа на `69ae53c65b66a9ad918c294d3f6f1e53192c47a0`; pytest **не** запускал |

---

## Out Of Scope

runtime этого PR; serve; mixed-stop; mixed gate; merge/retarget/deploy; исходное Test; повторное закрытие TASK-38.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | docs-контракт isolated drain/stop; статус **review (подготовлено)** |
| 2026-09-23 | точечно: финальный список WE после executor/continuation; registry daemon в полный graceful; cancel drain; sender handoff rollback / intake seal; D24–D30 |
| 2026-09-24 | GPT review `69ae53c…`; закрытие docs; runtime нет; срезы code — TASK-40+ |
