# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-03 |
| **Статус** | draft |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-02 (парсер), `ops/MODULAR_REORG_MIGRATION.md` |
| **Риск** | medium: трогает порядок импорта `scheduler.py` |

**Не реализовывать в PR #4 и не смешивать с TASK-2026-09-17-02.**

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
4. Известный профиль, для которого **ещё нет** isolated entry (`raccoon`, `wr`, и `antares` пока нет отдельного register): **завершить запуск** с ошибкой вида «profile X is not wired; refusing mixed JOB_REGISTRY» — **не** падать в legacy mixed scheduler.
5. Legacy mixed режим — только явный отдельный путь (например прежний unset на текущем Test **до** cutover), задокументированный как mixed, **не** как `antares`.

Нельзя подключать `PROJECT_PROFILE=raccoon` или `wr` к текущему `scheduler.py`, пока он регистрирует общий набор.

---

## Success Criteria (когда задача будет in_progress)

- [ ] До `import integrations.tg_commands` профиль уже отвергнут или выбран
- [ ] `PROJECT_PROFILE=raccoon` на legacy entry → exit, в `JOB_REGISTRY` пусто / процесс не живёт
- [ ] `PROJECT_PROFILE=wr` → то же, пока WR не реализован
- [ ] Нет silent fallback на другой проект

---

## Out Of Scope

Парсер (02); полный вынос Antares/Raccoon модулей; cutover `JOB_ACCEPT`; Railway.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Заведена по review PR #4: process gate ≠ parser |
