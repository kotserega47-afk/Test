# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-02 |
| **Статус** | draft |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-01, TASK-2026-09-17-03, `ops/MODULAR_REORG_MIGRATION.md` этап 1 |
| **Риск** | low для diff; merge в `test_main` может **рестартовать** сервис Test (автодеплой) |

К реализации **не переходить**, пока не закрыт review docs PR #4.

---

## Goal

Добавить **чистый парсер** `PROJECT_PROFILE`, пустые пакеты модулей и unit-тесты. Рабочие entrypoints **не** подключаются.

---

## Business Context

Первый code PR программы модульности. Не меняет набор jobs. Текущий Railway Test остаётся смешанным процессом (Antares + код Raccoon jobs). Это **не** изоляция профиля `antares`.

---

## Current Behavior

Профиля проекта нет. `scheduler.py` импортирует `tg_commands` → регистрирует общий `JOB_REGISTRY` (Antares + Raccoon + script_jobs) независимо от любой будущей строки env.

---

## Desired Behavior

Различать два уровня (в этом PR только первый):

| Уровень | Что делает | Этот PR |
|---------|------------|---------|
| Парсер | Разбирает строку; неизвестное значение → ошибка с перечислением допустимых; **не** запускает процесс, **не** импортирует jobs | **да** |
| Процесс | До побочных эффектов решает, какой модуль грузить; известный, но не реализованный профиль → выход **без** чужих jobs | **нет** (TASK-2026-09-17-03) |

Парсер:

- `parse_project_profile(value: str | None) -> Literal["antares","raccoon","wr"]` **или** typed error
- неизвестное значение (включая другой регистр, если не выбран явный lower-канон) → ошибка, **без** fallback на другой проект
- `None` / empty: вернуть `antares` + `implicit_default=True` **как разбор строки**, не как «процесс уже antares-only»
- пустые `modules/antares/__init__.py`, `modules/raccoon/__init__.py`, `modules/wr/__init__.py`: без I/O, без env, без регистрации jobs
- пакеты **не** импортируются из `scheduler.py`, `tg_commands.py`, `railway.toml`

**Запрещено в этом PR:**

- правки `scheduler.py`, `integrations/tg_commands.py`, `JOB_REGISTRY`, handlers, WE engine
- optional log / «интеграция одной строкой»
- подключение профилей `raccoon` / `wr` к legacy scheduler
- утверждение, что после merge процесс изолирован

---

## Affected Modules

| Модуль | Действие |
|--------|----------|
| `core/project_profile.py` | создать |
| `tests/unit/test_project_profile.py` | создать |
| `modules/*/__init__.py` | создать пустые |
| `scheduler.py` | **не трогать** |

---

## Success Criteria

- [ ] pytest `tests/unit/test_project_profile.py` зелёный
- [ ] неизвестная строка → ошибка парсера (тест)
- [ ] empty/None → implicit default `antares` (тест), без вызова scheduler
- [ ] `git diff` **не** содержит `scheduler.py`, `tg_commands.py`, `JOB_REGISTRY`, `railway.toml`, `automation/engine.py`
- [ ] `import modules.wr` в тесте не требует env и не ходит в сеть

**Не** критерий этого PR: «неизвестный профиль не стартует jobs» — это поведение **процесса** (TASK-2026-09-17-03). Парсер в unit-тесте не стартует scheduler.

---

## Out Of Scope

Early profile gate; `JOB_ACCEPT`; вырезание Raccoon из Test-процесса; порт Platform; WR adapter; DB; изменение Railway.

---

## Stop conditions

Любой import парсера/пакетов из prod entry → закрыть PR и убрать hook.

---

## Rollback

Revert commit. Если PR уже влит в `test_main`, revert тоже может вызвать restart Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Создан как следующая реализация после review docs |
| 2026-09-17 | Сужен: без scheduler log; парсер ≠ process gate |
