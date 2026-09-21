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
| [TASK-2026-09-17-04](TASK-2026-09-17-04_behavior_baseline.md) | Эталон поведения: подготовлен, review пройден, merge нет (PR #7) |
| [TASK-2026-09-17-05](TASK-2026-09-17-05_antares_job_registration.md) | Регистрация Antares jobs: review пройден, merge нет (PR #8) |
| [TASK-2026-09-17-06](TASK-2026-09-17-06_telegram_handler_split.md) | План TG handlers: review пройден, merge нет (PR #9) |
| [TASK-2026-09-17-07](TASK-2026-09-17-07_antares_run_handlers.md) | Четыре Antares run-команды: review пройден, merge нет (PR #10) |
| [TASK-2026-09-17-08](TASK-2026-09-17-08_antares_dispatch_commands.md) | Две Antares dispatch-команды: review пройден, merge нет (PR #11) |
| [TASK-2026-09-17-09](TASK-2026-09-17-09_registry_health_replay.md) | Registry health/replay: review пройден, merge нет (PR #12) |
| [TASK-2026-09-17-10](TASK-2026-09-17-10_registry_export.md) | Registry export: review пройден, merge нет (PR #13) |
| [TASK-2026-09-17-11](TASK-2026-09-17-11_auto_enable_cmds.md) | Auto-Enable callbacks: review пройден, merge нет (PR #14) |
| [TASK-2026-09-17-12](TASK-2026-09-17-12_document_ingest_plan.md) | План document ingest: review пройден, merge нет (PR #15) |
| [TASK-2026-09-17-13](TASK-2026-09-17-13_document_ingest.md) | Перенос document ingest: review пройден, merge нет (PR #16) |
| [TASK-2026-09-17-14](TASK-2026-09-17-14_antares_assembly_plan.md) | План сборки Antares: review пройден, merge нет (PR #17) |
| [TASK-2026-09-17-15](TASK-2026-09-17-15_script_job_bind.md) | Selective script JOB_REGISTRY bind: review пройден, merge нет (PR #18) |
| [TASK-2026-09-17-16](TASK-2026-09-17-16_antares_assembly.md) | Сборка Antares без запуска: review пройден, не выпущена, merge нет (PR #19) |
| [TASK-2026-09-17-17](TASK-2026-09-17-17_antares_entrypoint.md) | План isolated Antares entrypoint: review пройден, merge нет (PR #20) |
| [TASK-2026-09-17-18](TASK-2026-09-17-18_antares_boot.md) | Isolated Antares boot без polling: review пройден (`2c6eeac…`), не выпущен, merge нет (PR #21) |
| [TASK-2026-09-17-19](TASK-2026-09-17-19_antares_lifecycle.md) | Контракт lifecycle: первый `run` = local workbook + exit 0; уточнено к review, не пройден (PR #22) |

Программа: `ops/MODULAR_REORG_SURVEY.md`, `ops/MODULAR_REORG_ADR.md`, `ops/MODULAR_REORG_MIGRATION.md`.

## Старт без Task

Если задачи ещё нет — **Stage 0** в [NEW_CHAT_BOOTSTRAP.md](../NEW_CHAT_BOOTSTRAP.md).
