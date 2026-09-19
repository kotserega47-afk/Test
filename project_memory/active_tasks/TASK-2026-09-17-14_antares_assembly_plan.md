# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-14 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-13 (PR #16, закрытие `5f131ce50091a80cc03989d6fdf1e8b60f84b6ab`), `ops/MODULAR_REORG_ANTARES_ASSEMBLY.md`, ADR, MIGRATION, HANDLER_SPLIT |
| **PR** | Draft [#17](https://github.com/deniskotdavydov1991-wq/Test/pull/17) `feat/task-2026-09-17-14-antares-assembly-plan`, base `feat/task-2026-09-17-13-document-ingest` |
| **HEAD** | `1222a278fa46b55d578f956da4bf73c0602bc08e` |
| **Обследованный SHA** | `5f131ce50091a80cc03989d6fdf1e8b60f84b6ab` |
| **Риск** | low: только документы |

План минимальной самостоятельной сборки Antares **подготовлен к review**. Runtime и тесты в TASK-14 не менялись. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы.

---

## Goal

Зафиксировать состав Antares handlers/jobs, отказ от mixed `tg_commands` и Raccoon, отдельные help/status, исключение `run_script_hello`, этапы 1–4 и приёмку будущего code PR.

---

## Desired Behavior

Документ решения: этап 1 = собрать handlers/jobs без запуска; этапы 2–4 отдельно; mixed JSON и early gate без изменений.

---

## Success Criteria

- [x] Список isolated handlers (17 команд + document) и запрет raccoon/hello
- [x] Семь job keys; селективный script job `operator_wallets_ready`
- [x] Конкретные Antares help/status (новые callbacks; mixed тексты сохранены)
- [x] Карта зависимостей и side effects без `tg_commands`
- [x] Минимальный следующий code PR = только этап 1
- [ ] GPT review (ещё не пройден)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Обследование исходников SHA `5f131ce…`; pytest **не** требовался |
| GPT | ещё не проверял |

---

## Out Of Scope

runtime/тесты TASK-14; isolated entry; `JOB_ACCEPT`; cutover; Railway; профили production; merge #4–#16; ослабление early gate.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-19 | план сборки Antares подготовлен к review |
