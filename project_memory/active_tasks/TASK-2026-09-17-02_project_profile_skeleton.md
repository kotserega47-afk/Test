# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-02 |
| **Статус** | in_progress |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-01, TASK-2026-09-17-03, `ops/MODULAR_REORG_MIGRATION.md` этап 1 |
| **Риск** | low для diff; merge в `test_main` может **рестартовать** сервис Test (автодеплой) |

---

## Goal

Добавить **чистый парсер** профиля, пустые пакеты `modules/` и unit-тесты. Рабочие entrypoints **не** подключаются.

---

## Desired Behavior (parser contract)

`parse_project_profile(value: str | None) -> ProjectProfileSelection`

Frozen `ProjectProfileSelection`: `name: Literal["antares", "raccoon", "wr"]`, `implicit_default: bool`.

| Вход | Результат |
|------|-----------|
| `None`, `""`, только пробелы | `name="antares"`, `implicit_default=True` |
| `antares` / `raccoon` / `wr` после `strip` | тот же `name`, `implicit_default=False` |
| иное, включая `"Antares"` | `InvalidProjectProfileError`, без fallback |

Парсер не читает `os.environ`, не хранит mutable global state, не вызывает `SystemExit`, не пишет логи/файлы, не импортирует scheduler/Telegram/Playwright/jobs.

`implicit_default` — только разбор входа, не изоляция процесса. Принятие `"wr"` не означает готовность WR.

Process gate = TASK-2026-09-17-03 (не этот PR).

---

## Affected Modules

| Модуль | Действие |
|--------|----------|
| `core/project_profile.py` | создать |
| `tests/unit/test_project_profile.py` | создать |
| `modules/**/__init__.py` | создать пустые |
| `scheduler.py` | **не трогать** |

---

## Success Criteria

- [x] pytest `tests/unit/test_project_profile.py`
- [x] неизвестная строка / wrong case → ошибка
- [x] None / empty / whitespace → implicit antares
- [x] diff без hooks в entrypoints
- [x] import пакетов без production env

---

## Out Of Scope

TASK-03; `JOB_ACCEPT`; inbox/drain; Wallet Editor; Railway; JOB_REGISTRY.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Создан; сужен без scheduler |
| 2026-09-17 | Реализация парсера по контракту `ProjectProfileSelection` |
