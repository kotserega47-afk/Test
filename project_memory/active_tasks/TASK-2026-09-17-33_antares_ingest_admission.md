# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-33 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-32 close `7d3a463…`; TASK-30 accepted on combined PR #35 `c420b590…`; [MODULAR_REORG_ANTARES_INGEST_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_INGEST_ADMISSION.md); реализация — TASK-34 |
| **PR** | Draft [#36](https://github.com/deniskotdavydov1991-wq/Test/pull/36) `feat/task-2026-09-17-33-antares-ingest-admission`, base `feat/task-2026-09-17-32-rules-publish-generation` |
| **Риск** | medium: isolated ingest делит WE `Queue` с mixed; файл после download до put |

Review **пройден**. GPT проверил контракт на полном SHA `4682e3992adf4a02ef306bca5287fa96d1673efd`. GPT pytest **не** запускал. Runtime **не** менялся. Этот docs-коммит — закрытие TASK-33. Реализация **ещё не** выполнена (TASK-34). PR #36 остаётся Draft. Merge/deploy нет.

---

## Goal

Зафиксировать атомарный admit Telegram `.xlsx` на фактическом `queue.put`, владение `local_path` и матрицу тестов — без изменения Wallet Editor и без закрытия conversion/Auto-Enable/schedules.

---

## Success Criteria

- [x] Обязательный early reject после доступа/формата/operator и до «Файл получен»/download/worker; ранний OPEN не резервирует put
- [x] Accepted = `AdmissionQueued` после `put_nowait`; владение до await; `qsize` вне lock, диагностика
- [x] Все isolated исходы до Accepted: best-effort cleanup без затирания ошибки; cancel пробрасывается
- [x] После Accepted: нет unlink, нет retry, ошибка confirmation отдельно от постановки
- [x] Матрица Event/barrier включая I11–I18; mixed baseline
- [x] Lazy worker до отказа admit — ресурсный эффект, не drain/join
- [x] GPT review контракта на `4682e399…`; pytest GPT не запускал
- [ ] реализация (TASK-34)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | обследование исходников `4b1f3676507a11ea4582ab59eb9846bcac2849a6`; runtime не менялся; pytest **не** запускался |
| Cursor | уточнение контракта от review HEAD `1ebc024161dcb4f6876130f7a433aa2920ed55c8`; runtime не менялся; pytest **не** запускался |
| GPT | проверил контракт на `4682e3992adf4a02ef306bca5287fa96d1673efd`; runtime не менялся; pytest **не** запускал |

---

## Out Of Scope

runtime этого PR; реализация ingest admit (TASK-34); conversion bridge; `enqueue_auto_enable_batch`; schedules; drain/join/sender stop; mixed-stop; retarget существующих PR; live Telegram/очереди; исходное Test; повторное закрытие TASK-30/32.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | docs-контракт ingest admission; статус **review (подготовлено)** |
| 2026-09-22 | уточнение: обязательный early reject; владение сразу после put; cleanup всех исходов до Accepted; qsize диагностика; I11–I18 |
| 2026-09-22 | GPT review на `4682e399…`; закрытие docs; runtime нет; pytest GPT не запускал; реализация — TASK-34 |
