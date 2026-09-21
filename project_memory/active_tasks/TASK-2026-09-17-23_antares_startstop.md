# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-23 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-22 (PR #25, review `d9592ff0a04480433d18455726ca7f66f76d09bf`, закрытие `8a3fd5a2fb6d4738997b5463bebd8392e8db4f90`), `ops/MODULAR_REORG_ANTARES_STARTSTOP.md` |
| **PR** | Draft [#26](https://github.com/deniskotdavydov1991-wq/Test/pull/26) `feat/task-2026-09-17-23-antares-startstop`, base `feat/task-2026-09-17-22-antares-application-build` |
| **Риск** | low: только документы |

Контракт initialize / start / остановки **принят**. Runtime не менялся. Pytest **не** запускался. Сервисный lifecycle **не** реализован. Review **пройден** на HEAD `6540a36…`. GPT читал контракт. PR #26 остаётся Draft. Реализация sandbox helper — TASK-24.

---

## Goal

Зафиксировать production helper ручного lifecycle, единый порядок initialize→start→polling, очередь/error handler, staged initialize cleanup. Sandbox подтверждает этот helper, не отдельный test lifecycle.

---

## Success Criteria

- [x] Порядок везде: initialize → start → start_polling (polling только будущий serve)
- [x] Остановка: запрет новых updates → updater.stop если running → app.stop если running → app.shutdown / staged cleanup
- [x] Helper `run_ptb_lifecycle(app, stop=, enable_polling=)`: caller владеет Application; sandbox вызывает его же
- [x] Очередь: put Update, ждать callback, затем stop; process_update не доказательство fetcher
- [x] process_error / error handler; возврат process_update ≠ успех callback
- [x] Хвост очереди: по исходникам 22.8 не обещать drain и не обещать drop без проверки
- [x] Stop event vs cancel vs SIGINT vs SIGTERM; sandbox только event+cancel; asyncio.run ≠ SIGTERM
- [x] Initialize по трём стадиям; Bot.shutdown не покрывает все компоненты
- [x] do_request синтетика для getMe; JobQueue extra не в scope проверки
- [x] GPT review HEAD `6540a36…`: контракт прочитан; pytest не запускался; runtime не менялся; сервисный lifecycle не реализован
- [ ] merge/deploy (намеренно открыто)
- [ ] TASK-24 sandbox helper (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы по PTB 22.8 и коду на закрытии TASK-22; pytest не требовался |
| GPT | Прочитал контракт HEAD `6540a36…`. Pytest не запускал. Runtime не менялся. |

---

## Out Of Scope

runtime/тесты TASK-23; live Telegram; смена `run`; mixed gate; scheduler; profiles; requirements; Railway; STATE_DIR; remote/stale; cutover; merge; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | контракт start/stop подготовлен к review; Draft PR #26 |
| 2026-09-21 | уточнён исполнимый контракт: один порядок, production helper, очередь, staged initialize |
| 2026-09-21 | GPT review HEAD `6540a36…`: план принят; runtime не менялся; pytest не запускался; lifecycle не реализован |
