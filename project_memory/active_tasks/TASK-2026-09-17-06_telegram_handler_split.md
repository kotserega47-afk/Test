# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-06 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-05 (PR #8), `ops/MODULAR_REORG_HANDLER_SPLIT.md`, ADR E-MOD-01, MIGRATION этап 3 |
| **PR** | Draft [#9](https://github.com/deniskotdavydov1991-wq/Test/pull/9) `feat/task-2026-09-17-06-handler-plan`, base `feat/task-2026-09-17-05-antares-jobs` |
| **HEAD (проверен GPT)** | `380a4bb57d161ad39897cd32c5a6de2a64a57668` |
| **Риск** | low: только документы |

План разделения Telegram handlers **подготовлен**, review пройден. Контракт `bind_rules` / `bind_logger` принят. Следующий code scope — четыре Antares `run_*` и три общих helper; реализация TASK-07 **не** начата. Isolated entry и cutover **не** реализованы. PR #9 остаётся Draft.

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
- [x] Контракт `bind_rules` / `bind_logger` принят
- [x] GPT review HEAD `380a4bb…`: замечания закрыты, блокирующих нет
- [ ] merge/deploy PR #9 (намеренно открыто)
- [ ] реализация TASK-07 (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| GPT | Review документа решения и исходников, состав изменений HEAD `380a4bb…`. Pytest **не** запускал. |
| Cursor | Pytest для документационного этапа **не** запускался и не требуется. |

---

## Out Of Scope

Реализация handlers; isolated entry; cutover; изменение тестов/runtime; merge/deploy PR #4–#9; Railway; живые сервисы; TASK-07 (отдельное задание).

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Документ `ops/MODULAR_REORG_HANDLER_SPLIT.md`; следующий code — четыре Antares run-команды |
| 2026-09-17 | Контракт: единственный `bind_rules(RULES)`; logger параметром `run_job_async`; уточнены AST vs registration |
| 2026-09-17 | GPT review HEAD `380a4bb…`: блокирующих нет; merge/deploy нет; TASK-07 не начат |
