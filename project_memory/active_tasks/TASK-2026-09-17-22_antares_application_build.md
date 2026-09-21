# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-22 |
| **Статус** | review (к review; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-21 (PR #24, review `777a52f0e65bdc546185e45b023431c0c9c3d3c5`, закрытие `c83d3d6648c0d557fbe7bacf583c5e36b7ec1294`), `ops/MODULAR_REORG_ANTARES_APPLICATION.md` |
| **PR** | Draft (создаётся) `feat/task-2026-09-17-22-antares-application-build`, base `feat/task-2026-09-17-21-antares-application` |
| **Риск** | medium: isolated `run` строит реальный PTB Application без сети |

После успешного локального snapshot `python -m apps.antares run` строит `Application` и вешает `assembled.handlers`, затем печатает одну диагностику и **завершает процесс**. Boot без argv сохранён. initialize/start/get_me/polling/JobQueue.start **не** вызываются. Sender/worker/schedules **нет**. Не выпущено. **К review**, не «review пройден».

Завершение процесса — граница ресурсов только для одноразовой диагностики, не graceful shutdown сервиса.

---

## Goal

Собрать Telegram Application + те же Antares handlers после local snapshot, без запуска бота.

---

## Граница изменения

`apps/antares.py`: `run` после snapshot вызывает `Application.builder().token(token).concurrent_updates(True).build()` и `add_handler` для экземпляров `assembled.handlers`. Успех `run` только после всех handlers.

Тесты: harness наблюдает реальный builder/`add_handler`; инъекции `FAIL_BUILD` / `FAIL_ADD_HANDLER`; lifecycle-методы записываются и отклоняются.

Не менялись: `core/access_rules.py`, `rules_provider`, mixed gate, `requirements.txt`, `scheduler.py`.

---

## Success Criteria

- [x] Boot без argv / `boot`: TASK-18, Application не строится
- [x] `run`: сборка → local path → snapshot → build → те же 18 handlers → одна диагностика → exit 0
- [x] Token из boot-prefix, не печатается
- [x] Отказ сборки/пути/snapshot не достигает build
- [x] Отказ build / add_handler: конкретная причина, нет success, процесс завершается
- [x] Нет initialize/start/get_me/polling/JobQueue.start/shutdown ради lifecycle
- [ ] GPT review (ещё не пройден)
- [ ] merge/deploy (намеренно открыто)

---

## Прогоны (Cursor, Python 3.12.10, PTB 22.8, httpx 0.28.1)

job-queue extra: **нет** (`telegram.ext._jobqueue.APS_AVAILABLE is False`; default builder строит Application без JobQueue). Результат **не** обобщать на все `python-telegram-bot>=20.7` и не на Railway.

```
py -3.12 -m pytest tests/unit/test_antares_boot.py tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py -q --tb=short
```

**81 passed, 0 failed, 0 skipped, exit 0.**

Только boot/run/Application:

```
py -3.12 -m pytest tests/unit/test_antares_boot.py -q --tb=line -W default
```

**33 passed, 0 failed, 0 skipped, exit 0.** Новых ResourceWarning / httpx unclosed client на успешном `run` **не** было. В stderr ребёнка — прежний fail-closed лог WalletEditor ingest (`WALLET_EDITOR_ALLOWED_CHAT_IDS` пуст); предупреждения глобально не глушились.

GPT этот набор **не** запускал.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | 81 passed (33 boot/run + parser/gate) на этом PR |
| GPT | ещё не ревьюил |

---

## Out Of Scope

initialize/polling; worker; schedules; sender; `requirements.txt`; mixed gate; Railway; cutover; merge; исходное Test; живой token.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | Application build-only реализован; к review |
