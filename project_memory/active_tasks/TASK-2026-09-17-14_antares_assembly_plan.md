# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-14 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-13 (PR #16, закрытие `5f131ce50091a80cc03989d6fdf1e8b60f84b6ab`), `ops/MODULAR_REORG_ANTARES_ASSEMBLY.md`, ADR, MIGRATION, HANDLER_SPLIT |
| **PR** | Draft [#17](https://github.com/deniskotdavydov1991-wq/Test/pull/17) `feat/task-2026-09-17-14-antares-assembly-plan`, base `feat/task-2026-09-17-13-document-ingest` |
| **HEAD** | `94285eaec9e4577ce7b4576ee7f4b27087db665d` |
| **Обследованный SHA** | `5f131ce50091a80cc03989d6fdf1e8b60f84b6ab` |
| **Риск** | low: только документы |

План минимальной самостоятельной сборки Antares **подготовлен к review** (уточнён контракт registry, script bind и `/status`). Runtime и тесты в TASK-14 не менялись. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы.

---

## Goal

Зафиксировать состав Antares handlers/jobs, работу только через `JOB_REGISTRY` dispatch, split script_jobs без авторегистрации, точный `/status`, этапы 1–4 и приёмку.

---

## Desired Behavior

Документ решения: этап 1 пишет в фактический `JOB_REGISTRY`; подэтап 1 — script bind без package bootstrap; start/help/status только в `handlers.py`; mixed JSON и early gate без изменений.

---

## Success Criteria

- [x] Список isolated handlers (17 команд + document) и запрет raccoon/hello
- [x] Семь job keys на фактическом `JOB_REGISTRY`; отказ при чужих ключах без `clear()`
- [x] Selective script: `bind.py` + slim `__init__`; mixed явный bootstrap обоих jobs
- [x] Контракт Antares `/status` по блокам; locks = семь keys; владелец `handlers.py`
- [x] Следующий code: подэтап 1 (script import split), затем сборка
- [ ] GPT review (ещё не пройден)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Обследование исходников SHA `5f131ce…`; уточнение плана на `8fe4efbd…`; pytest **не** требовался |
| GPT | ещё не проверял |

---

## Out Of Scope

runtime/тесты TASK-14; isolated entry; `JOB_ACCEPT`; cutover; Railway; профили production; merge #4–#16; ослабление early gate.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-19 | план сборки Antares подготовлен к review |
| 2026-09-19 | уточнены JOB_REGISTRY identity, script bind без авторегистрации, контракт `/status` |
