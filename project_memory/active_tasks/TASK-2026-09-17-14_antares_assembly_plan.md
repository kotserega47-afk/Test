# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-14 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-13 (PR #16, закрытие `5f131ce50091a80cc03989d6fdf1e8b60f84b6ab`), `ops/MODULAR_REORG_ANTARES_ASSEMBLY.md` |
| **PR** | Draft [#17](https://github.com/deniskotdavydov1991-wq/Test/pull/17) `feat/task-2026-09-17-14-antares-assembly-plan`, base `feat/task-2026-09-17-13-document-ingest` |
| **HEAD (проверен GPT)** | `f123a4bf5482f45a5efacb2910ad0c7a4ade8171` |
| **Закрытие docs** | `e0f0400500d3d459067c4070cbbc8213df31fbed` |
| **Закрытие (pin SHA)** | `99d2db55027b94cb7739efd18ff349b838b22a21` |
| **Риск** | low: только документы |

План минимальной самостоятельной сборки Antares **принят**. Runtime сборки Antares в TASK-14 **не** реализовывался и **не** выпущен. Review пройден. Isolated entry, `JOB_ACCEPT` и cutover **не** реализованы. PR #17 остаётся Draft. Подэтап 1 (script registration) — TASK-15, review пройден (PR #18 Draft). Сборка handlers — TASK-16.

---

## Goal

Зафиксировать состав Antares handlers/jobs, работу только через `JOB_REGISTRY` dispatch, split script_jobs без авторегистрации, точный `/status`, этапы 1–4 и приёмку.

---

## Success Criteria

- [x] Список isolated handlers (17 команд + document) и запрет raccoon/hello
- [x] Семь job keys на фактическом `JOB_REGISTRY`; отказ при чужих ключах без `clear()`
- [x] Selective script: `bind.py` + slim `__init__`; mixed явный bootstrap обоих jobs
- [x] Контракт Antares `/status` по блокам; locks = семь keys; владелец `handlers.py`
- [x] Следующий code: подэтап 1 (script import split), затем сборка
- [x] GPT review HEAD `f123a4bf…`: блокирующих нет
- [ ] merge/deploy PR #17 (намеренно открыто)
- [x] code PR script bind / TASK-15 (Draft PR #18, review HEAD `6e4c6c4…`; merge нет)
- [ ] code PR сборка Antares / TASK-16 (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы; pytest **не** требовался |
| GPT | Проверил план HEAD `f123a4bf…`. Тесты **не** запускал. |

---

## Out Of Scope

runtime/тесты TASK-14; isolated entry; `JOB_ACCEPT`; cutover; Railway; профили production; merge #4–#17; ослабление early gate.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-19 | план сборки Antares подготовлен к review |
| 2026-09-19 | уточнены JOB_REGISTRY identity, script bind без авторегистрации, контракт `/status` |
| 2026-09-19 | GPT review HEAD `f123a4bf…`: план принят; merge/deploy нет |
| 2026-09-19 | документационное закрытие `e0f04005…`; PR #17 остаётся Draft |
| 2026-09-19 | TASK-15 (подэтап 1) review пройден HEAD `6e4c6c4…`; PR #18 Draft |
