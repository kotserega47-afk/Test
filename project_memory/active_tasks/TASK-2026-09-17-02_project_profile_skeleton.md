# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-02 |
| **Статус** | draft |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-01, `ops/MODULAR_REORG_ADR.md`, `ops/MODULAR_REORG_MIGRATION.md` этап 1 |
| **Риск** | low, если соблюдён Out of Scope |

---

## Goal

Добавить резолвер `PROJECT_PROFILE` и пустые пакеты модулей **без** изменения набора jobs, Telegram handlers и production start command.

---

## Business Context

Первый code PR программы модульности: можно влить и даже не деплоить. Antares должен стартовать как сейчас.

---

## Current Behavior

`scheduler.py` всегда собирает смешанный JOB_REGISTRY через import `tg_commands` → raccoon_jobs + script_jobs + Antares. Профиля проекта нет.

---

## Desired Behavior

- `core/project_profile.py`: `parse_project_profile(value) -> Literal["antares","raccoon","wr"]`
- Unknown → `SystemExit` / typed error с перечислением допустимых значений (без fallback на другой проект)
- `None` / empty: `antares` + функция `is_implicit_default()` для будущего warning
- `modules/antares/__init__.py`, `modules/raccoon/__init__.py`, `modules/wr/__init__.py` пустые, **не** импортируются из `scheduler.py` / `tg_commands.py`
- Тесты: unknown, empty, case-normalize (`Antares` → error **или** явный lower — выбрать fail-closed: неизвестный регистр = unknown)
- **Запрещено:** фильтровать JOB_REGISTRY; менять `railway.toml`; трогать WE engine

Опционально (если не раздувает PR): одна строка лога в `scheduler.main` «profile=… implicit=…» без ветвления jobs.

---

## Affected Modules

| Модуль | Действие |
|--------|----------|
| `core/project_profile.py` | создать |
| `tests/unit/test_project_profile.py` | создать |
| `modules/*/__init__.py` | создать пустые |
| `scheduler.py` | только optional log |

---

## Success Criteria

- [ ] pytest `tests/unit/test_project_profile.py` зелёный
- [ ] `git diff` не содержит `JOB_REGISTRY`, `get_handlers`, `automation/engine.py`, `railway.toml`
- [ ] import `modules.wr` не ходит в сеть и не требует env
- [ ] Неизвестный профиль не стартует jobs

---

## Out Of Scope

Вырезание Raccoon из Antares-процесса; порт Platform; WR adapter; DB; Railway; смена token env names.

---

## Stop conditions

Любое изменение состава handlers или lock paths → закрыть PR и сузить diff.

---

## Rollback

Revert commit. Данных нет.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Создан как следующая реализация после review docs |
