# Tasks & Gaps — `<PROJECT_NAME>`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Последнее обновление** | YYYY-MM-DD |

> **Как создать:** скопируйте в `project_memory/tasks.md`.  
> **G_** — gaps / UNKNOWN. **S_** — STALE_RISK (docs vs code).  
> Workflow-задачи живут в `active_tasks/TASK-*.md`.

---

## Open gaps (G#)

| ID | Gap / UNKNOWN | Impact | Owner | Статус |
|----|---------------|--------|-------|--------|
| G1 | Full prod env inventory | deploy / incident | ops | open |
| G2 | `<PLATFORM_UNKNOWN>` restart behavior | incident | ops | open |
| G3 | External caller of `<ENTRYPOINT>` | security / scope | arch | open |
| G4 | … | | | |

Закрытые gaps переносите в § «Закрыто» с датой и ссылкой на merge/task.

---

## STALE_RISK (S#)

| ID | Расхождение | Где | Severity | Action |
|----|-------------|-----|----------|--------|
| S1 | Legacy doc `<LEGACY_DOC>` vs KB | root / docs | med | mark DOCS_ONLY or update KB |
| S2 | Code path vs contract `<CONTRACT_NAME>` | `<MODULE>` | med | fix code or KB |
| S3 | … | | | |

---

## Операционные точки (one-shot)

| ID | Действие | Когда | Runbook |
|----|----------|-------|---------|
| OPS1 | Rollback deploy | failed release | `<LINK_OR_SECTION>` |
| OPS2 | Clear stale lock | stuck pipeline | `<PROJECT_LOCK_PATH>` policy |

---

## Закрыто в v1.1

| ID | Закрыто | Как |
|----|---------|-----|
| | YYYY-MM-DD | TASK-… / merge |

---

## Active workflow tasks (index)

Краткий индекс; полные файлы — `active_tasks/`.

| Task ID | Status | Goal (1 line) |
|---------|--------|---------------|
| | | |

---

## История

| Дата | Событие |
|------|---------|
| YYYY-MM-DD | Создан из `tasks_template.md` |
