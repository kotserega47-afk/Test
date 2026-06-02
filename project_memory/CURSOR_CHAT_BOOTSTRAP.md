# Cursor Chat Bootstrap

| Мета | Значение |
|------|----------|
| **Версия** | 1.0 |
| **Workflow** | Knowledge Workflow v1 |
| **Роль** | Cursor Implementation Executor |
| **Область** | `project_memory/` — KB и процесс (не runtime) |

Единая стартовая команда для **нового Cursor-чата** в любом проекте на Project OS.

---

## Standard Cursor Startup Command

```text
Прочитай:
project_memory/CURSOR_CHAT_BOOTSTRAP.md
```

После чтения выполни сценарий A / B / C ниже.

**Связанные документы:**

| Документ | Назначение |
|----------|------------|
| [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md) | Stage 0, Reading Order |
| [templates/gpt_bootstrap_template.md](templates/gpt_bootstrap_template.md) | Контекст роли GPT (handoff) |
| [roles.md](roles.md) | Implementation Executor |
| [README.md](README.md) | Reading Order, Document Map |
| [workflow.md](workflow.md) | Task lifecycle, merge |

---

## Core KB files (проверка наличия)

| Файл | Обязателен |
|------|------------|
| `project_memory/architecture_map.md` | да |
| `project_memory/contracts.md` | да |
| `project_memory/current_state.md` | да |
| `project_memory/decisions.md` | да |
| `project_memory/tasks.md` | да |

Если **любой** отсутствует → **Scenario A**.

---

## Scenario A — KB отсутствует

**Условие:** не все core KB файлы существуют.

### Обязанности Cursor

1. **Определить** отсутствующие файлы (список из таблицы выше).
2. Выполнить **Stage 0 — KB Initialization** (KB-only):
   - для каждого `templates/*_template.md` создать соответствующий `project_memory/*.md`;
   - оставить `<PLACEHOLDER>` там, где факты неизвестны — пометить **UNKNOWN**;
   - **не** заполнять выдуманными CONFIRMED-фактами.
3. **Не менять** runtime-код (вне `project_memory/`).
4. **Не начинать** implementation.
5. Показать **readiness score** для KB init (см. § Readiness score).

### Допустимые файлы (Scenario A)

- `project_memory/*.md` (создание KB из templates)
- `project_memory/templates/*`
- `README.md`, `TEMPLATE_CHECKLIST.md` (если есть)
- `active_tasks/*` — только если пользователь явно просит Task для KB init

### Вывод Cursor (формат)

```text
KB Status: INCOMPLETE → initializing
Created: <list of new KB files>
Placeholders remaining: <count / sections>
Readiness (KB init): <score>/10
Role: Cursor — KB scaffolding only, no implementation
Next: fill placeholders with project facts OR run TEMPLATE_CHECKLIST
```

---

## Scenario B — KB существует, Task отсутствует

**Условие:** все core KB файлы на месте; нет активной Task для текущего запроса.

### Обязанности Cursor

1. Прочитать [NEW_CHAT_BOOTSTRAP.md](NEW_CHAT_BOOTSTRAP.md).
2. Прочитать [templates/gpt_bootstrap_template.md](templates/gpt_bootstrap_template.md) (контекст handoff от GPT).
3. Прочитать [roles.md](roles.md).
4. Выполнить **Reading Order** из [README.md](README.md) (релевантные шаги, не весь repo).
5. Кратко вывести:
   - **Project summary** (из `architecture_map.md` § Назначение)
   - **Entry points** (`<PRIMARY_ENTRYPOINT>`, `<DEPLOYMENT_TARGET>` — CONFIRMED only)
   - **Pipelines** (ID + имя из `architecture_map.md`, без полных таблиц)
   - **Top gaps** (открытые G# из `tasks.md`, до 5)
   - **Active task** — «нет» или ID, если есть другие `active_tasks/TASK-*`
6. **Подтвердить роль:** Cursor Implementation Executor.
7. **Ждать** Task `ready` или явное поручение (например, «только создать Task markdown»).

### Запрещено (Scenario B)

- implementation / patch / tests без Task `ready`
- изменение KB без поручения
- scope expansion

### Вывод Cursor (формат)

```text
KB Status: READY
Role: Cursor Implementation Executor
Project summary: <brief>
Entry: <PRIMARY_ENTRYPOINT> | Deploy: <DEPLOYMENT_TARGET>
Pipelines: <P1 name>, <P2 name>, ...
Top gaps: G# ...
Active task: none | <TASK-ID>
Workflow step: awaiting Task ready
Next: provide Task path or ask to create TASK draft
```

---

## Scenario C — Task существует

**Условие:** есть `active_tasks/TASK-YYYY-MM-DD-NN_*.md`.

### Обязанности Cursor

1. Прочитать **Task** + **Context Pack** + **Impact** (если есть `_impact.md`).
2. Перечислить перед работой:
   - **Allowed files** (из Task scope / Out Of Scope inverse)
   - **Forbidden changes** (Constraints, DORMANT, DOCS_ONLY)
   - **Invariants** (E# / I# из `decisions.md`, релевантные Task)
   - **Affected runtime paths** (pipelines, modules из Task)
   - **Required tests** (из Task Success Criteria / Review)
3. Определить **Workflow step** по статусу Task.
4. **Не менять код**, пока Task не `ready` или `in_progress` (по [workflow.md](workflow.md)).
5. При `ready` → можно начинать implementation по CP + Reading Order.

### Вывод Cursor (формат)

```text
Task: <TASK-ID> | Status: <status>
Workflow step: <step>
Allowed files: <list or pattern>
Forbidden: <bullets from Constraints>
Invariants: E# / I# ...
Affected paths: pipelines <PX>, modules ...
Tests required: <from Task / Review>
Next: <implement | wait for ready | fix review blockers>
```

---

## Guardrails

| # | Правило |
|---|---------|
| C1 | **Minimal diff** — только scope Task |
| C2 | **No scope expansion** — вне Task / Out Of Scope запрещено |
| C3 | **No speculative refactor** |
| C4 | **No secrets** в коммитах, Task, Pack |
| C5 | **No runtime changes** без Task |
| C6 | **No implementation** до Task `ready` (или `in_progress` после ready) |
| C7 | **KB не менять** без поручения Architect / KB Owner |
| C8 | Сохранять поведение (**preserving invariants**) |

**Required output** после implementation — см. [roles.md](roles.md) § Cursor.

---

## Readiness score (Scenario A)

Оценка готовности KB после init (ориентир):

| Критерий | Балл |
|----------|------|
| 5 core KB файлов созданы | +5 |
| Ключевые `<PLACEHOLDER>` заменены (name, entrypoint) | +2 |
| Есть ≥1 pipeline в architecture_map | +1 |
| tasks.md содержит ≥1 G# или S# | +1 |
| Нет project-specific leaks (grep) | +1 |
| **Max** | **10** |

Порог для «можно начинать Task workflow»: **≥ 6/10** (файлы есть, placeholders частично заполнены).

---

## Required Next Inputs

| Ситуация | Нужно от пользователя |
|----------|------------------------|
| KB init (A) | Подтверждение создать KB из templates; факты проекта для placeholders |
| Ожидание Task (B) | `TASK-*.md` или просьба создать Task |
| Implementation (C) | Task `ready` + `CP-dev_task-*` (+ Impact если medium+) |

---

## Bootstrap block (после чтения)

Выведите **короткий блок** (5–15 строк): сценарий, роль, KB status, summary или Task scope, next step.

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Cursor Chat Bootstrap v1.0 |
