# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-33 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-32 close `7d3a463…`; TASK-30 accepted on combined PR #35 `c420b590…`; [MODULAR_REORG_ANTARES_INGEST_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_INGEST_ADMISSION.md) |
| **PR** | Draft [#36](https://github.com/deniskotdavydov1991-wq/Test/pull/36) `feat/task-2026-09-17-33-antares-ingest-admission`, base `feat/task-2026-09-17-32-rules-publish-generation` |
| **Риск** | medium: isolated ingest делит WE `Queue` с mixed; файл после download до put |

Только docs. Runtime **не** менялся. GPT review ещё не принимался. Реализация допуска ingest — следующий code, не этот PR. TASK-30 и TASK-32 повторно не закрывать.

---

## Goal

Зафиксировать атомарный admit Telegram `.xlsx` на фактическом `queue.put`, владение `local_path` и матрицу тестов — без изменения Wallet Editor и без закрытия conversion/Auto-Enable/schedules.

---

## Success Criteria

- [x] Три маршрута: фактическая точка `worker.queue.put` после `_ensure_profile_worker` (не весь `add_*_task` как атом)
- [x] Early check не заменяет проверку в lock вместе с `put_nowait`
- [x] Accepted = `put_nowait` внутри admission lock при OPEN; seal после этого не отменяет; сбой reply не retry
- [x] Владение файлом по исходам; после Accepted handler не unlink
- [x] Матрица Event/barrier; mixed baseline
- [x] Следующий code узкий: API + isolated ingest only
- [ ] GPT review контракта
- [ ] реализация (отдельный code PR)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование исходников `4b1f3676507a11ea4582ab59eb9846bcac2849a6`; runtime не менялся; pytest **не** запускался |
| GPT | ещё не ревьюил |

---

## Out Of Scope

runtime этого PR; conversion bridge; `enqueue_auto_enable_batch`; schedules; drain/join/sender stop; mixed-stop; retarget существующих PR; live Telegram/очереди; исходное Test; повторное закрытие TASK-30/32.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | docs-контракт ingest admission; статус **review (подготовлено)** |
