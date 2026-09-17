# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-06 |
| **Статус** | in_progress (docs; на review после Draft PR) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-05 (PR #8), `ops/MODULAR_REORG_HANDLER_SPLIT.md`, ADR E-MOD-01, MIGRATION этап 3 |
| **PR** | Draft, base `feat/task-2026-09-17-05-antares-jobs` |
| **Обследованный SHA** | `0ce4d5340fdbe5ea890c1a31a5cec6dfbbcff66f` |
| **Риск** | low: только документы |

---

## Goal

Конкретный план отделения Antares Telegram handlers, чтобы **будущий** isolated Antares entry собирал обработчики без импорта mixed `integrations.tg_commands`. В этой задаче нет кода handlers и нет isolated entry.

---

## Current Behavior (исходники)

Все 20 команд и document handler объявлены в `get_handlers()` (`integrations/tg_commands.py`). Help/status содержат mixed-перечни и raccoon locks. Четыре `run_*` Antares — тонкий ACL + `_run_job_async`. Raccoon и WE живут в том же модуле.

---

## Desired Behavior

Документ решения: карта владельцев, границы, контракт mixed vs isolated, один минимальный следующий code PR.

---

## Success Criteria

- [x] Карта 20 команд + document handler
- [x] Владельцы без «общее имя = независимый handler»
- [x] Script jobs по содержимому (`hello_world` vs `operator_wallets_ready`)
- [x] Минимальный code PR без scheduler/cutover/полного переноса
- [x] Mixed `expected_tg_commands.json` не предлагается менять
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

Реализация handlers; isolated entry; изменение тестов/runtime; merge PR #4–#8; Railway; живые сервисы.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Документ `ops/MODULAR_REORG_HANDLER_SPLIT.md`; следующий code — четыре Antares run-команды |
