# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-39 |
| **Статус** | review (уточнено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-38 close `9221f052f8b9bacda10a3041757fa72f7687202c` (review `f12811035433bce0306ef9ef9d328db43652795a`, Draft PR #41); [MODULAR_REORG_ANTARES_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) |
| **PR** | Draft [#42](https://github.com/deniskotdavydov1991-wq/Test/pull/42) `feat/task-2026-09-17-39-antares-drain-stop`, base `feat/task-2026-09-17-38-antares-auto-enable-enqueue-impl` |
| **Риск** | high: общий executor, WE daemon, sender `run_forever`; mixed-stop не этот docs |

Контракт drain/stop **уточнён**. Runtime **не** менялся. Реализация **не** начата. TASK-38 повторно не закрывать. TASK-39 **не** закрывать. Merge/deploy нет.

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
- [x] Матрица D1–D30; O1–O9 сведены; O10 mixed-stop до cutover
- [ ] GPT review
- [ ] реализация
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | точечное уточнение по review HEAD `dbc7ce4b80ff8326ace5cea25efd40eb6c9b7cdd`: финальный WE список после AE, registry join, cancel drain, sender rollback; runtime нет; pytest не требовался |
| GPT | pytest **не** запускал (ожидается review) |

---

## Out Of Scope

runtime; serve; mixed-stop; mixed gate; merge/retarget/deploy; исходное Test; закрытие TASK-38/39.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | docs-контракт isolated drain/stop; статус **review (подготовлено)** |
| 2026-09-23 | точечно: финальный список WE после executor/continuation; registry daemon в полный graceful; cancel drain; sender handoff rollback / intake seal; D24–D30 |
