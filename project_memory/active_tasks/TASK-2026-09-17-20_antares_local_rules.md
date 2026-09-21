# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-20 |
| **Статус** | review (ожидает GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-19 (PR #22, review `b76a377bacc437e72b066f4ccb12f7fc29557b7c`, закрытие `8d806477d86ddb88dbc1ae5bbccce32d10bef15e`), `ops/MODULAR_REORG_ANTARES_LIFECYCLE.md` § 4.3 / § 11 |
| **PR** | Draft [#23](https://github.com/deniskotdavydov1991-wq/Test/pull/23) `feat/task-2026-09-17-20-antares-local-rules`, base `feat/task-2026-09-17-19-antares-lifecycle` |
| **Риск** | medium: isolated `run` читает локальный xlsx через реальный provider |

Диагностический `python -m apps.antares run`: boot-prefix + существующий локальный `.xlsx` + `rules.get_snapshot(force_sync=True)` → печать → **exit 0**. Application, polling, worker, schedules, sender **не** стартуют. `AccessRules` / `rules_provider` / mixed gate **не** менялись. Review **не** отмечать принятым заранее.

---

## Goal

Отделить boot (сборка без workbook) от `run` (локальная диагностика правил с завершением процесса).

---

## Граница изменения

Изменено: `apps/antares.py` (argv `boot`/`run`, проверка `RULES_XLSX_PATH` до snapshot).

Тесты: harness записывает `snapshot_called` / `rules_download_attempted` и блокирует download; sandbox audit/identity.

Не менялись: `core/access_rules.py`, политика `rules_provider`, `scheduler.py`, mixed gate.

---

## Success Criteria

- [x] Без argv и `boot`: прежняя сборка, exit 0, workbook не читается
- [x] `run`: prefix + local xlsx + snapshot; успех без строки `antares boot ok`
- [x] Неверный путь: отказ до snapshot; download не вызывается
- [x] Повреждённый xlsx / отказ publish: ненулевой exit, нет успешной диагностики
- [x] Конфликт сборки: snapshot не вызывается
- [x] Неизвестные argv: понятная ошибка, без сборки
- [ ] GPT review
- [ ] merge/deploy

---

## Прогоны (Cursor, Python 3.12.10)

```
py -3.12 -m pytest tests/unit/test_antares_boot.py tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py -q --tb=short
```

**78 passed, 0 failed, 0 skipped, exit 0.**

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | прогон выше |
| GPT | ещё не проверял |

---

## Out Of Scope

Application; polling; worker; schedules; sender shutdown; смена `rules_provider`; mixed gate; `JOB_ACCEPT`; Railway; cutover; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | диагностический `run` по локальному workbook; Draft PR #23; статус review |
