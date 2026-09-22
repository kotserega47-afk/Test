# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-33 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-32 close `7d3a463…`; TASK-30 accepted on combined PR #35 `c420b590…`; [MODULAR_REORG_ANTARES_INGEST_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_INGEST_ADMISSION.md) |
| **PR** | Draft [#36](https://github.com/deniskotdavydov1991-wq/Test/pull/36) `feat/task-2026-09-17-33-antares-ingest-admission`, base `feat/task-2026-09-17-32-rules-publish-generation` |
| **Риск** | medium: isolated ingest делит WE `Queue` с mixed; файл после download до put |

Только docs. Runtime **не** менялся. Уточнение контракта на review HEAD `1ebc024…`. GPT review ещё не принимался. TASK-33 **не** закрывать. Реализация — следующий code PR. TASK-30 и TASK-32 повторно не закрывать.

---

## Goal

Зафиксировать атомарный admit Telegram `.xlsx` на фактическом `queue.put`, владение `local_path` и матрицу тестов — без изменения Wallet Editor и без закрытия conversion/Auto-Enable/schedules.

---

## Success Criteria

- [x] Обязательный early reject после доступа/формата/operator и до «Файл получен»/download/worker; ранний OPEN не резервирует put
- [x] Accepted = успех `put_nowait`; владение фиксируется до await/log/reply; qsize — диагностика
- [x] Все isolated исходы до Accepted: best-effort cleanup без затирания ошибки; cancel пробрасывается
- [x] После Accepted: нет unlink, нет retry, ошибка confirmation отдельно от постановки
- [x] Матрица Event/barrier включая I11–I18; mixed baseline
- [x] Lazy worker до отказа admit — ресурсный эффект, не drain/join
- [ ] GPT review уточнённого контракта
- [ ] реализация (отдельный code PR)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование исходников `4b1f3676507a11ea4582ab59eb9846bcac2849a6`; runtime не менялся; pytest **не** запускался |
| Cursor | уточнение контракта от review HEAD `1ebc024161dcb4f6876130f7a433aa2920ed55c8`; runtime не менялся; pytest **не** запускался |
| GPT | ещё не ревьюил |

---

## Out Of Scope

runtime этого PR; conversion bridge; `enqueue_auto_enable_batch`; schedules; drain/join/sender stop; mixed-stop; retarget существующих PR; live Telegram/очереди; исходное Test; повторное закрытие TASK-30/32.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | docs-контракт ingest admission; статус **review (подготовлено)** |
| 2026-09-22 | уточнение: обязательный early reject; владение сразу после put; cleanup всех исходов до Accepted; qsize диагностика; I11–I18 |
