# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-03 |
| **Статус** | in_progress |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-02 (парсер), `ops/MODULAR_REORG_MIGRATION.md` |
| **Риск** | medium: трогает порядок импорта `scheduler.py` |

Реализация: gate в `core/project_profile_boot.py`, вызов в `scheduler.py` **до** Telegram/jobs. Unset `PROJECT_PROFILE` = documented **legacy mixed**, не isolated Antares.

---

## Goal

Рабочий процесс проверяет `PROJECT_PROFILE` **до** импорта модулей, которые требуют env, пишут на диск, ставят Playwright или регистрируют `JOB_REGISTRY`.

---

## Current Behavior

`scheduler.py` сразу импортирует `integrations.tg_commands` (и дальше downloader / raccoon_jobs / script_jobs). Побочные эффекты импорта происходят **до** любой будущей проверки профиля. Legacy scheduler всегда собирает **смешанный** набор jobs.

---

## Desired Behavior

1. Прочитать env профиля (без импорта downloaders).
2. Вызвать парсер из TASK-2026-09-17-02.
3. Неизвестное значение → выход с понятной ошибкой, **без** чужих jobs.
4. Известный профиль **без** isolated entry (`raccoon`, `wr`, и явный `antares` пока нет отдельного register) → **отклонить** запуск: «profile X is not wired; refusing mixed JOB_REGISTRY».
5. Legacy mixed — **только** отдельный compat path (например documented unset / отдельный entry), **не** `PROJECT_PROFILE=antares`.

Нельзя подключать `PROJECT_PROFILE=antares|raccoon|wr` к текущему mixed `scheduler.py`.

---

## Success Criteria (когда задача будет in_progress)

- [x] До `import integrations.tg_commands` профиль уже отвергнут или выбран
- [x] `PROJECT_PROFILE=antares` на mixed legacy entry → exit (не silent mixed)
- [x] `PROJECT_PROFILE=raccoon` на legacy entry → exit
- [x] `PROJECT_PROFILE=wr` → exit, пока WR не wired
- [x] Нет silent fallback на другой проект
- [x] Mixed-compat path остаётся отдельно от явного `antares`

---

## Out Of Scope

Парсер (02); полный вынос Antares/Raccoon модулей; cutover `JOB_ACCEPT`; Railway.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Заведена по review PR #4: process gate ≠ parser |
