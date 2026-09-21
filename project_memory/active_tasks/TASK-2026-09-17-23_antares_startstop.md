# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-23 |
| **Статус** | review (подготовлено; **не** «пройден»; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-22 (PR #25, review `d9592ff0a04480433d18455726ca7f66f76d09bf`, закрытие `8a3fd5a2fb6d4738997b5463bebd8392e8db4f90`), `ops/MODULAR_REORG_ANTARES_STARTSTOP.md` |
| **PR** | Draft (создаётся) `feat/task-2026-09-17-23-antares-startstop`, base `feat/task-2026-09-17-22-antares-application-build` |
| **Риск** | low: только документы |

Контракт initialize / start / остановки **подготовлен к review**. Runtime не менялся. Boot и диагностический `run` сохраняются. Live polling в `run` **не** добавляется. Выбран **ручной async** lifecycle, не `run_polling`. Pytest **не** запускался. Статус: не «review пройден».

---

## Goal

Зафиксировать владельца loop, порядок PTB start/stop, cleanup частичного initialize, последствия первого update и минимальный sandbox code без live getUpdates.

---

## Success Criteria

- [x] Ручной async выбран и обоснован; `run_polling` для isolated запрещён
- [x] Loop: isolated `asyncio.run`; не `Application.__run`
- [x] Порядок initialize / start / polling и обратной остановки по PTB 22.8
- [x] Частичный initialize: `Application.shutdown` no-op vs `Bot.shutdown` / `AsyncClient.aclose`
- [x] SIGINT/Windows, очередь updates, JobQueue extra vs нет
- [x] Первый update может поднять jobs/worker/sender/ingest; sandbox без live polling
- [x] `run` остаётся диагностикой; будущий сервис — отдельный argv
- [x] Зависимости graceful shutdown названы (JOB_ACCEPT, sender, worker, executor)
- [ ] GPT review (ещё не пройден)
- [ ] merge/deploy (намеренно открыто)
- [ ] sandbox lifecycle code (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы по PTB 22.8 и коду на закрытии TASK-22; pytest не требовался |
| GPT | ещё не ревьюил |

---

## Out Of Scope

runtime/тесты TASK-23; live Telegram; смена `run`; mixed gate; scheduler; profiles; requirements; Railway; STATE_DIR; remote/stale; cutover; merge; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | контракт start/stop подготовлен к review |
