# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-37 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-36 close `c9c75336cd12dc5182608790159055aa5c73482a` (review `3f6d3e75dd4874efb9020374bf82360771092f2e`, Draft PR #39); [MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md](../ops/MODULAR_REORG_ANTARES_AUTO_ENABLE_ENQUEUE.md) |
| **PR** | Draft [#40](https://github.com/deniskotdavydov1991-wq/Test/pull/40) `feat/task-2026-09-17-37-antares-auto-enable-enqueue`, base `feat/task-2026-09-17-36-antares-schedules-admission-impl` |
| **Риск** | medium: shared WE `CONVERSION_AUTO` queue; mixed `enqueue_auto_enable_batch` не менять этим docs |

Контракт внутреннего Auto-Enable enqueue. Runtime **не** менялся. Реализация **ещё не** выполнена. TASK-36 повторно не закрывать. Merge/deploy нет. PR [#40](https://github.com/deniskotdavydov1991-wq/Test/pull/40) остаётся Draft.

---

## Goal

Зафиксировать единственный code-протокол: continuation на `WorkAdmission` + wrapper в `executor.submit` + проверка в `enqueue_auto_enable_batch` до ensure/put.

---

## Success Criteria

- [x] Callers enqueue; conversion не caller
- [x] Accepted = submit wrapper `run_auto_enable`; не put, не имя fn, не executor-поток, не contextvars
- [x] Lifecycle: register до submit; revoke в wrapper finally; queued-after-seal; TG cancel не отзывает
- [x] Isolated прямой `enqueue_auto_enable_batch` без token — отказ до ensure/put; mixed unbound прежний
- [x] Нет окна enqueue после revoke (тот же job thread + lock owner_thread)
- [x] Ошибки до/после put; Future-once; log после set_result
- [x] Граница worker для обычных AE exception; dead worker — отложенный блокер
- [x] Матрица E1–E15
- [ ] GPT review
- [ ] реализация
- [ ] merge/deploy (намеренно открыто)

---

## Выбранный протокол

Запись на instance `WorkAdmission`. Opaque token. Wrapper в `submit` ставит thread-local на job thread. `enqueue_auto_enable_batch`: если `bound_admission()` — `require_valid_…` до ensure/put. Unbound = mixed, без fallback «нет token ⇒ mixed».

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование worker/handlers на `3f6d3e7…` / close `c9c7533…`; runtime не менялся; pytest не требовался |
| GPT | pytest **не** запускал (ожидается review) |

---

## Out Of Scope

runtime; mixed enqueue/add_task; conversion в первый isolated; serve; drain/join; live кабинеты/Telegram/сеть; merge/retarget/deploy; исходное Test; повторное закрытие TASK-36.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-23 | docs-контракт Auto-Enable enqueue continuation; статус **review (подготовлено)** |
| 2026-09-23 | протокол выбран: WorkAdmission token + submit wrapper + gate в `enqueue_auto_enable_batch`; E9–E15 |
