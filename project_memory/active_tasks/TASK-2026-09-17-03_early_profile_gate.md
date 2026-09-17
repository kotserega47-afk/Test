# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-03 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-02 (парсер), `ops/MODULAR_REORG_MIGRATION.md` |
| **PR** | Draft [#6](https://github.com/deniskotdavydov1991-wq/Test/pull/6) `feat/task-2026-09-17-03-profile-gate` |
| **HEAD (проверен GPT)** | `48a2a825ce3d04d27501d1a387bb5c4b8b5dd9c9` |
| **Риск** | medium: трогает порядок импорта `scheduler.py`; непустое `PROJECT_PROFILE` на сервисе Test **откажет запуск** |

Реализация: gate в `core/project_profile_boot.py`, вызов в `scheduler.py` **до** Telegram/jobs. Unset / пустая / пробельная `PROJECT_PROFILE` = documented **legacy mixed**, не isolated Antares. Явные `antares` / `raccoon` / `wr` отклоняются, пока нет isolated entry.

---

## Goal

Рабочий процесс проверяет `PROJECT_PROFILE` **до** импорта модулей, которые требуют env, пишут на диск, ставят Playwright или регистрируют `JOB_REGISTRY`.

---

## Current Behavior (после реализации, до merge)

- Legacy mixed — **текущий рабочий режим** при отсутствии непустого `PROJECT_PROFILE`.
- Явные профили **пока отклоняются** (`SystemExit` 2).
- Модули проектов **ещё не выделены**; jobs/handlers не переносились.
- Merge/deploy **не выполнены**.

---

## Desired Behavior

1. Прочитать env профиля (без импорта downloaders).
2. Вызвать парсер из TASK-2026-09-17-02.
3. Неизвестное значение → выход с понятной ошибкой, **без** чужих jobs.
4. Известный профиль **без** isolated entry → **отклонить**: «refusing mixed JOB_REGISTRY».
5. Legacy mixed — только compat path (unset / `""` / whitespace), **не** `PROJECT_PROFILE=antares`.

---

## Success Criteria

- [x] До `import integrations.tg_commands` профиль уже отвергнут или выбран
- [x] Явные `antares` / `raccoon` / `wr` на mixed entry → exit
- [x] Нет silent fallback на другой проект
- [x] Mixed-compat отдельно от явного `antares`
- [x] `python scheduler.py` без/`""`/`"   "` PROJECT_PROFILE: wiring `main` через заглушки (не бизнес-результат jobs)
- [x] GPT review HEAD `48a2a82…`: блокирующих замечаний нет
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `python -m pytest tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py` → **41 passed** на **Python 3.13.14** |
| GPT | Проверил код и diff PR #6 (HEAD `48a2a82…`). **Этот набор тестов повторно не запускал.** |

Stubs доказывают wiring (handlers → Application, `ensure_worker_started`, `schedule_loop`, `run_polling`), не реальные отчёты/jobs.

---

## Out Of Scope

Парсер (02); полный вынос Antares/Raccoon модулей; cutover `JOB_ACCEPT`; Railway; merge.

---

## Перед выпуском (не эта задача)

Отдельно проверить: автодеплой Test на `test_main`; активные задания; **отсутствие непустого `PROJECT_PROFILE`** у текущего сервиса Test. После выката TASK-03 непустое значение → отказ запуска.

Порядок после согласования выпуска: слить **#4** → переназначить base **#5** и проверить diff → слить **#5** → переназначить base **#6** и проверить diff. Review до переназначения **не** считать автоматически действующим, если код изменился.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Заведена по review PR #4: process gate ≠ parser |
| 2026-09-17 | PR #6: subprocess `scheduler.py` + stubs; reload убран |
| 2026-09-17 | Изоляция harness на `sys.executable`; GPT review без блокирующих |
