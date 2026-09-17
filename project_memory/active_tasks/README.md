# Active Tasks

Рабочая директория для артефактов **Knowledge Workflow v1**.

## Что здесь хранится

| Тип файла | Шаблон | Пример имени |
|-----------|--------|--------------|
| Task | `templates/task_template.md` | `TASK-2026-05-24-01_short-title.md` |
| Impact Analysis | `templates/impact_analysis_template.md` | `TASK-2026-05-24-01_short-title_impact.md` |
| Review | `templates/review_template.md` | `TASK-2026-05-24-01_short-title_review.md` |
| Context Pack | `templates/context_pack_template.md` | `CP-dev_task-TASK-2026-05-24-01-20260524.md` |

## Правила

1. **Один Task — один файл**; impact и review — суффиксы `_impact`, `_review`.
2. **Pack ID:** `CP-<scenario>-<TASK-ID>-<YYYYMMDD>` — см. [context_pack.md](../context_pack.md) § 3.5.
3. В pack и task **только не-done** задачи в секции Active Tasks.
4. После `done` / `cancelled` — перенос pack в `archive/` (рекомендуется).
5. Не дублировать KB целиком — ссылки и краткая выжимка.

## Статусы Task

`draft` → `ready` → `in_progress` → `review` → merge → `done` | `cancelled`

См. [workflow.md](../workflow.md) § 3.

## Активные (2026-09-17)

| ID | Тема |
|----|------|
| [TASK-2026-09-17-01](TASK-2026-09-17-01_modular_reorg_survey.md) | Обследование и проект миграции (Draft PR #4, не слит) |
| [TASK-2026-09-17-02](TASK-2026-09-17-02_project_profile_skeleton.md) | Парсер профиля: реализован, review пройден, merge нет (PR #5) |
| [TASK-2026-09-17-03](TASK-2026-09-17-03_early_profile_gate.md) | Early gate: реализован, review пройден, merge нет (PR #6) |
| [TASK-2026-09-17-04](TASK-2026-09-17-04_behavior_baseline.md) | Эталон текущего поведения (`in_progress`) |

Программа: `ops/MODULAR_REORG_SURVEY.md`, `ops/MODULAR_REORG_ADR.md`, `ops/MODULAR_REORG_MIGRATION.md`.

## Старт без Task

Если задачи ещё нет — **Stage 0** в [NEW_CHAT_BOOTSTRAP.md](../NEW_CHAT_BOOTSTRAP.md).
