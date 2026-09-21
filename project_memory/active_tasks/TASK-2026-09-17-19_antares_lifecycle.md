# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-19 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-18 (PR #21, review `2c6eeac34940f70413be70da35ddbc81e720ec83`, закрытие `d1d11e308c1abc9b0a5ee4531c3249559b16c5a0`, тесты `0603eb9ac42c3c04b282df6b38ed804b62db7307`), `ops/MODULAR_REORG_ANTARES_LIFECYCLE.md`, `ops/MODULAR_REORG_ANTARES_ENTRYPOINT.md` § 4 |
| **PR** | Draft [#22](https://github.com/deniskotdavydov1991-wq/Test/pull/22) `feat/task-2026-09-17-19-antares-lifecycle`, base `feat/task-2026-09-17-18-antares-boot` |
| **HEAD (принятый review)** | `b76a377bacc437e72b066f4ccb12f7fc29557b7c` |
| **Риск** | low: только документы |

Контракт запуска и остановки isolated Antares **принят**. Runtime, `rules_provider`, mixed gate, Railway и профили **не** менялись. Pytest **не** запускался. Настоящий lifecycle сервиса (Application, polling, worker, schedules, sender) **не** реализован. Review **пройден** на HEAD `b76a377…`. PR #22 остаётся Draft. Реализация диагностического `run` — TASK-20.

Опора: принятый план entrypoint TASK-17 и фактический boot TASK-18.

---

## Goal

Зафиксировать выполнимый lifecycle: сохранить boot; отдельно `run`; первый code — локальный xlsx + snapshot без UNKNOWN источника; `force_sync`/`STRICT` не считать свежестью; фильтр семи keys и stop — позже; изоляция token/files vs process-local.

---

## Success Criteria

- [x] Boot без argv = TASK-18 (сборка, exit 0, нет polling)
- [x] `run` — отдельный argv; запрет после неуспешной сборки; snapshot не вызывать
- [x] Первый `run`: существующий локальный `RULES_XLSX_PATH` после dotenv, иначе отказ до snapshot
- [x] Путь только из env; конструктор `AccessRules` аргумент не использует
- [x] `force_sync` ≠ свежесть remote; STRICT ≠ защита от `_RULES_LOCAL` в свежем процессе
- [x] Успешная диагностика ≠ Dropbox freshness / сервис / cutover
- [x] Remote download и disk-cache fallback не входят в успех этого подэтапа
- [x] Отдельное будущее решение: remote source и stale reuse до запуска сервиса
- [x] Таблица start/stop ресурсов; фильтр семи keys — не первый code
- [x] Контракт subprocess-тестов (sandbox audit/identity)
- [x] GPT review HEAD `b76a377…`: план принят; runtime не менялся; тесты не запускал
- [ ] merge/deploy PR #22 (намеренно открыто)
- [ ] code PR argv + local snapshot / TASK-20 (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы по исходникам; pytest **не** требовался |
| GPT | Проверил план HEAD `b76a377…`. Тесты **не** запускал. |

---

## Out Of Scope

runtime/тесты TASK-19; реализация `run` в этом PR; смена `rules_provider` / mixed gate; polling; worker; schedules; sender shutdown; `JOB_ACCEPT`; Railway; cutover; merge/retarget/deploy; исходное дерево Test; живой workbook/prod env.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | контракт lifecycle подготовлен к review; Draft PR #22 |
| 2026-09-21 | уточнён первый подэтап: local `RULES_XLSX_PATH`, пределы `force_sync`/`STRICT`, тесты sandbox; review не пройден |
| 2026-09-21 | GPT review HEAD `b76a377…`: план принят; runtime не менялся; pytest не запускался; сервисный lifecycle не реализован |
