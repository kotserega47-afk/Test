# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-37 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-36 close `c9c75336cd12dc5182608790159055aa5c73482a` (review `3f6d3e75dd4874efb9020374bf82360771092f2e`, Draft PR #39); [MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md](../ops/MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md) |
| **PR** | Draft (этот PR) `feat/task-2026-09-17-37-antares-auto-enable-enqueue`, base `feat/task-2026-09-17-36-antares-schedules-admission-impl` |
| **Риск** | medium: shared WE `CONVERSION_AUTO` queue; mixed `enqueue_auto_enable_batch` не менять этим docs |

Контракт внутреннего Auto-Enable enqueue. Runtime **не** менялся. Реализация **ещё не** выполнена. TASK-36 повторно не закрывать. Merge/deploy нет.

---

## Goal

Зафиксировать: новый внешний вход после seal запрещён; внутренний `queue.put` батча — continuation конкретного Accepted `/auto_enable_run`; прямой caller без токена не обход; ошибки enqueue не оставляют вечный `result_future`.

---

## Success Criteria

- [x] Обследованы callers `enqueue_auto_enable_batch` (TG isolated/mixed, conversion **не** caller)
- [x] Accepted = `submit_if_open(run_auto_enable)`, не `Queue.put` и не «поток executor»
- [x] Протокол continuation; lock-order без admission lock на Future/batch
- [x] Матрица E1–E8 Event/barrier; conversion/legacy вне первого isolated
- [ ] GPT review
- [ ] реализация
- [ ] merge/deploy (намеренно открыто)

---

## Выбранный протокол

Continuation token принятого `/auto_enable_run`. Seal режет новый submit. Уже Accepted run может enqueue после seal. `put_nowait_if_open` на батч **не** единственный затвор.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование `3f6d3e7…` / close `c9c7533…`; runtime не менялся; pytest не требовался |
| GPT | pytest **не** запускал (ожидается review контракта) |

---

## Out Of Scope

runtime; mixed enqueue/add_task; conversion в первый isolated; serve; drain/join; live кабинеты/Telegram/сеть; merge/retarget/deploy; исходное Test; повторное закрытие TASK-36.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | docs-контракт Auto-Enable enqueue continuation; статус **review (подготовлено)** |
