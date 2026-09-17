# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-04 |
| **Статус** | in_progress |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-01 survey, TASK-02/03 (parser+gate, не слиты), `ops/MODULAR_REORG_SURVEY.md` § 4, `ops/MODULAR_REORG_MIGRATION.md` этап 2 |
| **Риск** | low, если только docs/golden на обезличенных входах; **не** менять runtime, Railway, профили |

---

## Goal

Зафиксировать **текущее** поведение Test (и отличия Raccoon vs Platform_2.0) как эталон **до** физического переноса модулей. Результат — артефакты для регрессии при будущем move, не смена режима запуска.

---

## Business Context

После TASK-02/03 рабочий режим всё ещё **legacy mixed**. Перенос файлов без эталона сломает отчёты, команды и расписания незаметно. Нужна фиксация «как есть», чтобы этап выделения модулей сверялся с ней.

---

## Current Behavior

- Сервис Test: смешанный `scheduler.py` (Antares + регистрация Raccoon jobs + WE), пока `PROJECT_PROFILE` не задан.
- Явные профили на этом entry **отклоняются** (TASK-03, не в prod, пока PR не слит).
- Raccoon prod — отдельный сервис Platform `develop` (F26); код Raccoon в Test **не** эквивалентен Platform (survey § 4.1–4.2).
- Модули `modules/{antares,raccoon,wr}` пустые.

---

## Desired Behavior

Только документация и воспроизводимые проверки на **обезличенных** входах (фикстуры, stubs). Не запускать живые кабинеты, не слать отчёты, не трогать кошельки.

Поставить в репозиторий (или явно сослаться, если уже есть):

1. **Эталонные результаты отчётов** на анонимизированных входах (hourly/wallet/conversion/payout/raccoon render — что реально покрыто golden/характеризацией). Пометить пробелы, где эталона нет.
2. **Перечень команд и расписаний** текущего mixed Test: Telegram handlers, `JOB_REGISTRY` job_type, откуда триггер (rules `schedules` / hardcoded / только TG).
3. **Различия Raccoon Test vs Platform_2.0**: scheduler, locks, token env, команды, триггеры, chat/import-time требования — сверка и уточнение survey § 4, без копирования Platform в Test.
4. **Проверки, которые обязаны сохраняться при переносе модулей** (минимальный набор: WE terminal/unread chips, add-block Save, PID locks Test, mixed job names, command list, report golden).

---

## Affected Modules

Документы: `ops/MODULAR_REORG_*`, при необходимости `tests/` golden на фикстурах.  
Runtime jobs/handlers/WE/Railway: **не менять**.

---

## Success Criteria

- [x] Таблица команд + job_type + триггер для текущего Test mixed
- [x] Указатель существующих golden/characterization и список отчётов без эталона
- [x] Явный diff-конспект Raccoon T vs P (не «считать одинаковым»)
- [x] Чеклист регрессии для будущего move (что должно остаться зелёным)
- [x] Нет переноса файлов в `modules/*`, нет isolated entry, нет merge #4/#5/#6 в рамках этой задачи

---

## Out Of Scope

Физический перенос модулей; переключение `PROJECT_PROFILE` на сервисах; isolated Antares/Raccoon/WR entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4/#5/#6; живые выгрузки и рассылка.

---

## Constraints

- Секреты и персональные данные в эталоны не класть.
- Не считать survey § 4 автоматически полным эталоном — задача **проверяет и фиксирует**, что ещё должно войти в чеклист.
- Legacy mixed остаётся рабочим режимом.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Создана после review TASK-02/03: этап 2 миграции, без move |
| 2026-09-17 | Артефакт `ops/MODULAR_REORG_BEHAVIOR_BASELINE.md`; payin golden; inventory freeze |
| 2026-09-17 | PR #7: Platform compare via `--platform-checkout`; AST vs assembled JOB_REGISTRY |
