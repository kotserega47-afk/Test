# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-17 |
| **Статус** | review (ожидает GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-16 (PR #19, review `f29cc8918eef022e0a4b88b1ac7f59a67212798f`, закрытие `a05ee7f7d06d3dd801b58181de963d4832d9bfd9`, pin `950a66a745bbac64cbf2281951a5ed28e01474cf`), `ops/MODULAR_REORG_ANTARES_ENTRYPOINT.md` |
| **PR** | Draft [#20](https://github.com/deniskotdavydov1991-wq/Test/pull/20) `feat/task-2026-09-17-17-antares-entrypoint`, base `feat/task-2026-09-17-16-antares-assembly` |
| **HEAD** | `bf1a1353ea89854c1431ce3037549bdc40278d79` |
| **Риск** | low: только документы |

План отдельного Antares process entry **подготовлен к review**. Runtime, mixed gate, Railway и профили **не** менялись. Isolated entrypoint, polling, worker, schedules, `JOB_ACCEPT` и cutover **не** реализованы. Сборка TASK-16 остаётся реализованной и **не** выпущенной.

---

## Goal

Зафиксировать контракт isolated `apps/antares.py`: допустимый профиль и ранний отказ, порядок gate → rules/logger → assembly, запрет старта при сбое сборки, будущий start/stop, зависимости sender/worker/schedules/state, минимальный code PR и subprocess-проверки.

---

## Current Behavior (исходники на pin TASK-16)

- Единственный process entry: `python scheduler.py`; prod `tini` + тот же файл.
- Mixed gate до Telegram/jobs: явный `antares`/`raccoon`/`wr` → exit 2.
- `telegram_bot`: `load_dotenv()` и daemon sender loop на **import**.
- `assemble_antares` собирает семь keys, не стартует polling/worker/schedules.
- `load_schedules` без фильтра job_type; locks в `STATE_DIR/locks`.

---

## Desired Behavior

Документ принят как контракт. Код entry в TASK-17 **не** пишется. Следующий code — boot entry без polling (см. план § 6).

---

## Success Criteria

- [x] Путь `apps/antares.py` и команда; mixed/Railway без изменений
- [x] Только `PROJECT_PROFILE=antares`; отказ unset/raccoon/wr/unknown до Telegram/jobs
- [x] Порядок isolated gate → dotenv/token → rules/logger → assembly; сбой сборки без polling/worker/schedules
- [x] Будущий порядок старта/остановки компонентов
- [x] Факты: token, routes, workbook, `.env`, sender loop, Dropbox, schedules, STATE_DIR/tmp/очереди, worker/engine
- [x] Минимальный code scope + subprocess-проверки; факты / UNKNOWN / блокеры разделены
- [ ] GPT review: блокирующих нет
- [ ] merge/deploy (намеренно открыто)
- [ ] code PR boot entry (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы по исходникам pin `950a66a…`; pytest **не** требовался |
| GPT | ещё не проверял этот diff |

---

## Out Of Scope

runtime/тесты TASK-17; реализация `apps/antares.py`; polling; worker; schedules; ослабление mixed gate; Railway; `JOB_ACCEPT`; cutover; merge/retarget/deploy #4–#19; исходное дерево Test / ветка Wallet Editor.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | план isolated Antares entrypoint подготовлен к review; Draft PR #20 |
