# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-20 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-19 (PR #22, review `b76a377bacc437e72b066f4ccb12f7fc29557b7c`, закрытие `8d806477d86ddb88dbc1ae5bbccce32d10bef15e`), `ops/MODULAR_REORG_ANTARES_LIFECYCLE.md` § 4.3 / § 11 |
| **PR** | Draft [#23](https://github.com/deniskotdavydov1991-wq/Test/pull/23) `feat/task-2026-09-17-20-antares-local-rules`, base `feat/task-2026-09-17-19-antares-lifecycle` |
| **HEAD (принятый review)** | `8b42e4daadbdcdd71a0843170d859ea98d190208` |
| **Риск** | medium: isolated `run` читает локальный xlsx через реальный provider |

Диагностический `python -m apps.antares run` **реализован, не выпущен**. Boot без argv сохранён. Application, polling, worker, schedules и sender **не** запускаются. Review **пройден** на HEAD `8b42e4d…`. GPT смотрел код/diff, наборы тестов **не** запускал. PR #23 остаётся Draft. Следующий шаг — TASK-21 (Application + handlers без polling).

---

## Goal

Отделить boot (сборка без workbook) от `run` (локальная диагностика правил с завершением процесса).

---

## Граница изменения

Изменено: `apps/antares.py` (argv `boot`/`run`, проверка `RULES_XLSX_PATH` до snapshot).

Тесты: harness наблюдает реальный `evaluate_snapshot_publish` / `ContractPublishRejected` (не подменяет результат); `snapshot_called` с `force_sync`; download блокируется. Sandbox audit/identity.

Не менялись: `core/access_rules.py`, политика `rules_provider`, `scheduler.py`, mixed gate.

---

## Success Criteria

- [x] Без argv и `boot`: прежняя сборка, exit 0, workbook не читается
- [x] `run`: prefix + local xlsx + snapshot; успех без строки `antares boot ok`
- [x] Неверный путь: отказ до snapshot; download не вызывается
- [x] Повреждённый xlsx: ненулевой exit, нет успешной диагностики (отдельный сценарий)
- [x] Отказ publish: `ContractPublishRejected` + `RULE_EMPTY_JOBS` (не общий ValueError/harness)
- [x] Успешный `run`: assembly раньше snapshot, `force_sync=True`, нет download, есть run-диагностика, нет boot
- [x] Конфликт сборки (foreign registry и bind): конкретная причина, snapshot не вызывается
- [x] Неизвестные argv: понятная ошибка, без сборки
- [x] GPT review HEAD `8b42e4d…`: код/diff; наборы не запускал; блокирующих нет
- [ ] merge/deploy PR #23 (намеренно открыто)
- [ ] TASK-21 Application без polling (отдельное задание)

---

## Прогоны (Cursor, Python 3.12.10)

Исторический набор на HEAD `cd23a7ab20a8e16e9ccf870bfdb475ccc880b92b` (boot/run + parser/gate):

```
py -3.12 -m pytest tests/unit/test_antares_boot.py tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py -q --tb=short
```

**78 passed, 0 failed, 0 skipped, exit 0.**

Уточнение доказательств snapshot на HEAD `8b42e4daadbdcdd71a0843170d859ea98d190208`:

```
py -3.12 -m pytest tests/unit/test_antares_boot.py -q --tb=short
```

**31 passed, 0 failed, 0 skipped, exit 0.**

GPT проверил код/diff, оба набора **не** запускал.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | 78 passed на `cd23a7a…`; 31 passed на `8b42e4d…` |
| GPT | код/diff принятого HEAD; тесты не запускал |

---

## Out Of Scope

Application; polling; worker; schedules; sender shutdown; смена `rules_provider`; mixed gate; `JOB_ACCEPT`; Railway; cutover; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | диагностический `run` по локальному workbook; Draft PR #23; статус review |
| 2026-09-21 | уточнены доказательства publish/`force_sync`/assembly conflicts; TASK-20 не закрыт |
| 2026-09-21 | GPT review HEAD `8b42e4d…`: boot/run приняты, не выпущены; Application не стартовал |
| 2026-09-21 | закрытие TASK-20: review пройден на `8b42e4d…`; 78 passed Cursor `cd23a7a…`; 31 passed Cursor `8b42e4d…` Python 3.12.10; GPT код/diff, наборы не запускал; не выпущено |
