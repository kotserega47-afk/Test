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
| [TASK-2026-09-17-19](TASK-2026-09-17-19_antares_lifecycle.md) | Контракт lifecycle: review пройден (`b76a377…`), runtime не менялся, merge нет (PR #22) |
| [TASK-2026-09-17-20](TASK-2026-09-17-20_antares_local_rules.md) | Диагностический `run` по локальному workbook: review пройден (`8b42e4d…`), не выпущен, merge нет (PR #23) |
| [TASK-2026-09-17-21](TASK-2026-09-17-21_antares_application.md) | Контракт Application + handlers без polling: review пройден (`777a52f…`), runtime не менялся, merge нет (PR #24) |
| [TASK-2026-09-17-22](TASK-2026-09-17-22_antares_application_build.md) | Isolated `run` Application build-only: review пройден (`d9592ff…`), не выпущено, merge нет (PR #25) |
| [TASK-2026-09-17-23](TASK-2026-09-17-23_antares_startstop.md) | Контракт initialize/polling/stop: review пройден (`6540a36…`), закрытие `94951c6…`, merge нет (PR #26) |
| [TASK-2026-09-17-24](TASK-2026-09-17-24_antares_ptb_lifecycle.md) | Sandbox PTB lifecycle helper: review пройден (`34ef7af…`), закрытие `3649764…`, 15/96 Cursor, не выпущено, merge нет (PR #27) |
| [TASK-2026-09-17-25](TASK-2026-09-17-25_antares_work_admission.md) | Контракт допуска: review пройден (`eda7144…`), закрытие `a33df9c…`, merge нет (PR #28) |
| [TASK-2026-09-17-26](TASK-2026-09-17-26_antares_run_wallet_admission.md) | WorkAdmission + isolated `/run_wallet`: review пройден (`960bf69…`), Cursor 62/52, GPT код/тесты + AdmittedJob 3.12.14 PASS, не выпущено, merge нет (PR #29) |
| [TASK-2026-09-17-27](TASK-2026-09-17-27_antares_dispatch_admission.md) | Isolated admission для шести dispatch-команд: review пройден (`a84e9cd…`), закрытие `08132f2…`, Cursor 90/52, GPT код/diff без этих pytest, не выпущено, merge нет (PR #30) |
| [TASK-2026-09-17-28](TASK-2026-09-17-28_antares_direct_ops_admission.md) | Isolated допуск export/Auto-Enable: review пройден (`f0bd06b…`), закрытие `a81aa69…`, Cursor 120/21 на review HEAD, 52 lifecycle/boot исторически на `a5836de…`, GPT исходники без pytest, не выпущено, merge нет (PR #31) |
| [TASK-2026-09-17-29](TASK-2026-09-17-29_antares_registry_replay_admission.md) | Isolated допуск `/registry_replay`: review пройден (`71fce3b…`), закрытие `d3eecc0…`, Cursor 129/52, GPT код/diff без pytest, не выпущено, merge нет (PR #32) |
| [TASK-2026-09-17-30](TASK-2026-09-17-30_antares_reload_rules_admission.md) | Isolated допуск `/reload_rules`: review пройден на объединённом PR #35 `c420b590…` (GPT код/diff, pytest не запускал; Cursor 161; исторические 130 на `bb25f734…`); PR #33 `1eefc54` не самостоятельная принятая версия; не выпущено, merge нет |
| [TASK-2026-09-17-31](TASK-2026-09-17-31_rules_publish_generation.md) | Контракт публикации поколений: review GPT на `8ef2838…`, закрытие docs `3d56791…`, runtime нет, pytest не запускался, merge нет (PR #34) |
| [TASK-2026-09-17-32](TASK-2026-09-17-32_rules_publish_generation.md) | Code согласованной публикации: GPT review на `bb25f734…` (pytest GPT не запускал); Cursor 70 на `bb25f734…`, 123/34/30/52 на `5bbde8f…`; закрытие docs `7d3a463…`; PR #35 Draft; не выпущено |
| [TASK-2026-09-17-33](TASK-2026-09-17-33_antares_ingest_admission.md) | Контракт допуска Telegram document ingest: GPT review на `4682e399…`, закрытие docs `cecb336…`, runtime нет, pytest GPT не запускал, реализация TASK-34, Draft PR #36 |
| [TASK-2026-09-17-34](TASK-2026-09-17-34_antares_ingest_admission.md) | Isolated ingest admission code: GPT review на `f76f9c9…`, закрытие docs, Cursor 28/95, не выпущено, Draft PR #37 |
| [TASK-2026-09-17-35](TASK-2026-09-17-35_antares_schedules_admission.md) | Контракт допуска isolated schedules: GPT review на `ce5326a…`, закрытие docs `0be29ec…`, runtime нет, реализация TASK-36, Draft PR #38 |
| [TASK-2026-09-17-36](TASK-2026-09-17-36_antares_schedules_admission.md) | Isolated schedule tick: GPT review на `3f6d3e7…`, закрытие docs, Cursor 31/7/84/52, не выпущено, Draft PR #39 |
| [TASK-2026-09-17-37](TASK-2026-09-17-37_antares_auto_enable_enqueue.md) | Контракт Auto-Enable continuation: GPT review на `741f5cc…`, закрытие docs, runtime нет, реализация TASK-38, Draft PR #40 |

Программа: `ops/MODULAR_REORG_SURVEY.md`, `ops/MODULAR_REORG_ADR.md`, `ops/MODULAR_REORG_MIGRATION.md`.

## Старт без Task

Если задачи ещё нет — **Stage 0** в [NEW_CHAT_BOOTSTRAP.md](../NEW_CHAT_BOOTSTRAP.md).
