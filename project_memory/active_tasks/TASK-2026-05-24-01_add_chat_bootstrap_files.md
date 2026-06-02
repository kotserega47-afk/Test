# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-05-24-01 |
| **Статус** | done |
| **KB версия** | v1.1 (template) |
| **Связанные артефакты** | `CP-dev_task-TASK-2026-05-24-01-20260524.md`, `TASK-2026-05-24-01_add_chat_bootstrap_files_review.md` |
| **Impact** | не требуется (KB/template-only) |

---

## Goal

Добавить в Project OS два универсальных chat bootstrap-файла (`GPT_CHAT_BOOTSTRAP.md`, `CURSOR_CHAT_BOOTSTRAP.md`) и обновить документацию, чтобы во всех будущих проектах были единые стартовые команды для GPT и Cursor.

---

## Business Context

Шаблон Project OS используется как база для новых проектов. Без единых entrypoints каждый чат начинается по-разному, что ломает Knowledge Workflow v1. Задача — template-level улучшение без runtime и без project-specific данных.

---

## Current Behavior

- Старт чата описан в `NEW_CHAT_BOOTSTRAP.md` и `gpt_bootstrap_template.md` без отдельных GPT/Cursor entrypoints.
- Нет стандартных команд «Прочитай …» для нового чата.
- Quick Start в `project_memory/README.md` не выделяет GPT vs Cursor.

---

## Desired Behavior

- `GPT_CHAT_BOOTSTRAP.md` — сценарии A/B/C/D, guardrails, required inputs для GPT.
- `CURSOR_CHAT_BOOTSTRAP.md` — сценарии A/B/C, guardrails, readiness score для KB init.
- Документация обновлена: `README.md`, `project_memory/README.md`, `NEW_CHAT_BOOTSTRAP.md`, `TEMPLATE_CHECKLIST.md`.
- Workflow artifacts: Task, CP, Review (без Impact).

---

## Affected Modules

| Модуль / файл | Статус в KB | Действие |
|---------------|-------------|----------|
| `GPT_CHAT_BOOTSTRAP.md` | new | создать |
| `CURSOR_CHAT_BOOTSTRAP.md` | new | создать |
| `project_memory/README.md` | template | изменить |
| `NEW_CHAT_BOOTSTRAP.md` | template | изменить |
| root `README.md` | template | изменить |
| `TEMPLATE_CHECKLIST.md` | template | изменить |
| `PROJECT_OS_AUDIT.md` | template | изменить (readiness) |

---

## Affected Pipelines

| ID | Пайплайн | Затронут |
|----|----------|----------|
| — | нет (KB-only) | нет |

---

## Affected Contracts

| Контракт | Breaking? | Комментарий |
|----------|-----------|---------------|
| — | не применимо | документация процесса |

---

## Constraints

- Не добавлять runtime-код.
- Не использовать project-specific examples (analizis, Database_of_operations, и т.д.).
- Не менять семантику Workflow v1.
- Только allowed files из Task scope.

---

## Known Risks

| Риск | Источник | Вероятность | Митигация |
|------|----------|-------------|-----------|
| Дублирование с NEW_CHAT_BOOTSTRAP | overlap docs | low | cross-links, разделение ролей |
| Leak scan false positive | checklist grep list | low | исключить audit/checklist files |

---

## Success Criteria

- [x] `GPT_CHAT_BOOTSTRAP.md` создан с сценариями A–D и guardrails
- [x] `CURSOR_CHAT_BOOTSTRAP.md` создан с сценариями A–C и guardrails
- [x] `project_memory/README.md` — Document Map + Quick Start
- [x] root `README.md` — раздел Starting GPT and Cursor chats
- [x] `NEW_CHAT_BOOTSTRAP.md` ссылается на оба entrypoint
- [x] `TEMPLATE_CHECKLIST.md` — пункт про chat bootstrap
- [x] Task + CP + Review созданы
- [x] Grep leak scan clean (кроме checklist/audit lists)

---

## Out Of Scope

- Изменение `workflow.md` semantics
- Создание core KB files (`architecture_map.md`, …)
- Runtime code, CI, pre-commit hooks
- Impact Analysis

---

## Workflow decisions

| Решение | Значение |
|---------|----------|
| Impact required? | **нет** — template/KB-only |
| Context Pack required? | **да** — `dev_task` |
| KB update post-merge? | **нет** — только template docs |

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-05-24 | Template Engineer | Task created (`draft` → `ready` → `done`) |
