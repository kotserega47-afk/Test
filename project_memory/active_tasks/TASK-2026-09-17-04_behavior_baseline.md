# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-04 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-2026-09-17-01 survey, TASK-02/03 (parser+gate, не слиты), `ops/MODULAR_REORG_BEHAVIOR_BASELINE.md` |
| **PR** | Draft [#7](https://github.com/deniskotdavydov1991-wq/Test/pull/7) `feat/task-2026-09-17-04-behavior-baseline`, base `feat/task-2026-09-17-03-profile-gate` |
| **HEAD (проверен GPT)** | `443ba70ef7b0052aa92fa7a9b00acbf5fa2f66fa` |
| **Риск** | low: docs/golden/isolated compare; runtime jobs/handlers/WE/Railway **не** менялись |

Эталон текущего поведения **подготовлен**, review пройден. Перенос модулей и переключение профилей **не начинались**. PR #7 остаётся Draft.

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
- Raccoon prod — отдельный сервис Platform `develop` (F26); код Raccoon в Test **не** эквивалентен Platform.
- Модули `modules/{antares,raccoon,wr}` пустые.

---

## Desired Behavior (эта задача)

Только документация и воспроизводимые проверки на **обезличенных** входах. Не запускать живые кабинеты, не слать отчёты, не трогать кошельки.

1. Эталонные результаты отчётов на анонимизированных входах; явные пробелы.
2. Перечень команд и `JOB_REGISTRY` (регистрация в коде ≠ production schedule).
3. Различия Raccoon Test vs Platform_2.0 без копирования модулей Platform.
4. Чеклист регрессии для будущего move.

---

## Affected Modules

Документы: `ops/MODULAR_REORG_BEHAVIOR_BASELINE.md`, golden/inventory tests.  
Runtime jobs/handlers/WE/Railway: **не менять**.

---

## Success Criteria

- [x] Таблица команд + job_type + триггер для текущего Test mixed
- [x] Указатель существующих golden/characterization и список отчётов без эталона
- [x] Явный diff-конспект Raccoon T vs P (не «считать одинаковым»)
- [x] Чеклист регрессии для будущего move
- [x] AST-инвентаризация отделена от собранного `JOB_REGISTRY` / `get_handlers()`
- [x] Platform compare: `--platform-checkout`, pin SHA, чистый tree, изоляция sender/rules/сеть/потоки
- [x] GPT review HEAD `443ba70e…`: прежние замечания закрыты, блокирующих нет
- [x] Нет переноса файлов в `modules/*`, нет isolated entry
- [ ] merge/deploy PR #7 (намеренно открыто)

---

## Пробелы покрытия (явные)

Полный перенос модулей **не** готов. Осталось без воспроизводимого эталона:

- wallet file→DTO;
- отдельный payout-report;
- conversion Excel cell-level;
- raccoon daily conversion full text;
- Platform wallet-analyzer.

Регистрация jobs / список команд **не** доказывает, что job включён в production `rules.xlsx` schedules.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | **94 passed** на HEAD `493c776e…` (связанный набор Test, включая inventory/registration/goldens/parser+gate). **Не** повторялся на `443ba70e…`. |
| Cursor | Platform compare: **1 passed** на HEAD `443ba70e…` |
| Cursor | Test payin golden: **1 passed** на HEAD `443ba70e…` |
| GPT | Проверил код и diff HEAD `443ba70e…`. Наборы 94 / Platform 1 / payin 1 **независимо не запускал.** |

---

## Out Of Scope

Физический перенос модулей; переключение `PROJECT_PROFILE`; isolated entry; `JOB_ACCEPT`; cutover; merge/deploy PR #4/#5/#6/#7; живые выгрузки и рассылка.

---

## Constraints

- Секреты и персональные данные в эталоны не класть.
- Legacy mixed остаётся рабочим режимом.
- Не расширять тестовую обвязку в рамках закрытия review.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-17 | Создана после review TASK-02/03: этап 2 миграции, без move |
| 2026-09-17 | Артефакт `ops/MODULAR_REORG_BEHAVIOR_BASELINE.md`; payin golden; inventory freeze |
| 2026-09-17 | PR #7: Platform compare via `--platform-checkout`; AST vs assembled JOB_REGISTRY |
| 2026-09-17 | Изоляция Platform compare (stubs sender/rules, блок сети/потоков) |
| 2026-09-17 | GPT review HEAD `443ba70e…`: блокирующих нет; merge/deploy нет |
