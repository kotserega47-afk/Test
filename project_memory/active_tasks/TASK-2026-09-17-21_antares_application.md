# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-21 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-20 (PR #23, review `8b42e4daadbdcdd71a0843170d859ea98d190208`, закрытие `5bed1b0308775a769481409e18e20644b181967d`), `ops/MODULAR_REORG_ANTARES_APPLICATION.md`, `ops/MODULAR_REORG_ANTARES_LIFECYCLE.md` |
| **PR** | Draft [#24](https://github.com/deniskotdavydov1991-wq/Test/pull/24) `feat/task-2026-09-17-21-antares-application`, base `feat/task-2026-09-17-20-antares-local-rules` |
| **HEAD (принятый review)** | `777a52f0e65bdc546185e45b023431c0c9c3d3c5` |
| **Риск** | low: только документы |

Контракт следующего code **принят**. После успешного локального snapshot — Telegram `Application` и Antares handlers **без** initialize/start/polling. Runtime, mixed gate, Railway **не** менялись. Pytest **не** запускался. Application в процессе **не** создаётся этим PR. Review **пройден** на HEAD `777a52f…`. GPT читал контракт, тесты не запускал. PR #24 остаётся Draft. Реализация — TASK-22.

Завершение процесса после диагностики — граница ресурсов **только** для одноразового diagnostic run, не graceful shutdown сервиса. `HTTPXRequest.shutdown()` закрывает внутренний `httpx.AsyncClient.aclose()`; метода `HTTPXRequest.aclose()` нет.

Опора: закрытие TASK-20 и PTB **22.8**.

---

## Goal

Зафиксировать API `build()`, ресурсы и границы методов; не считать `build()` запуском или авторизацией token; не обещать `Application.shutdown()` до initialize; описать минимальный code и subprocess с реальным `Application.build()`.

---

## Success Criteria

- [x] Точный API: `Application.builder().token(…).concurrent_updates(True).build()` (как mixed)
- [x] Ресурсы `build()`: ExtBot, два HTTPXRequest (`httpx.AsyncClient` на construct), Updater, default JobQueue; Telegram HTTP нет до `get_me` / `do_request`
- [x] Границы build / initialize / start / polling / shutdown по исходникам 22.8
- [x] Cleanup после одного `build()`: процесс exit; `Application.shutdown`/`Bot.shutdown` no-op без initialize; initialize ради shutdown **запрещён**
- [x] Частичный сбой build/add_handler: нет success, нет polling, процесс завершается
- [x] Минимальный code scope: boot TASK-18; run = сборка → local snapshot → Application + `assembled.handlers`; 17+document, порядок и identity; без initialize/polling/sender/worker/schedules
- [x] Контракт subprocess: реальный build и handlers; сеть запрещена
- [x] GPT review HEAD `777a52f…`: контракт прочитан; pytest не запускался; runtime не менялся
- [ ] merge/deploy (намеренно открыто)
- [ ] TASK-22 Application build-only (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы по PTB 22.8 и коду на закрытии TASK-20; pytest **не** требовался |
| GPT | Прочитал контракт HEAD `777a52f…`. Pytest не запускал. Runtime не менялся. |

---

## Out Of Scope

runtime/тесты TASK-21; реализация Application в этом PR; initialize/polling; worker; schedules; sender; смена `rules_provider` / mixed gate; `JOB_ACCEPT`; Railway; cutover; merge/retarget/deploy; исходное дерево Test; живой Telegram / рабочий token.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | контракт Application без запуска подготовлен к review; Draft PR #24 |
| 2026-09-21 | GPT review HEAD `777a52f…`: план принят; pytest не запускался; runtime не менялся; терминология `AsyncClient.aclose` |
