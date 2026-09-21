# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-24 |
| **Статус** | review (к GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-23 (PR #26, review `6540a36ebe19408e76e8e8b9f1cbc20116c64d97`, закрытие `94951c6ff4b3e9137f81151fac81e6fe2739e77a`), `modules/antares/application_lifecycle.py` |
| **PR** | Draft [#27](https://github.com/deniskotdavydov1991-wq/Test/pull/27) `feat/task-2026-09-17-24-antares-ptb-lifecycle`, base `feat/task-2026-09-17-23-antares-startstop` |
| **Риск** | medium: реальный PTB initialize/start/stop/shutdown в sandbox без live Telegram |

Production helper `run_ptb_lifecycle(app, *, stop, enable_polling=False)` исполняет initialize → start → ожидание `stop` → stop → shutdown. Caller владеет Application и loop. Boot и диагностический `run` helper **не** вызывают. Публичный serve **не** добавлен. `enable_polling=True` отклоняется **до** initialize. Polling-ветка не реализована.

Review **ещё не пройден**. PR остаётся Draft. **Не выпущено.**

---

## Goal

Проверить тот же production helper в sandbox: очередь `/whoami`, stop event, cancellation, staged initialize/start и ошибки cleanup.

---

## Граница изменения

- `modules/antares/application_lifecycle.py` — helper + инспекция leftover httpx.
- Тесты: subprocess `python -m run_lifecycle` в `tests/unit/antares_lifecycle_harness/` (sitecustomize + child). Не копирует порядок helper в pytest runner.
- Не менялись: `apps/antares.py` boot/run, `requirements.txt`, mixed gate, `scheduler.py`.

PTB internals localized in the helper (не global monkeypatch production): `Application._initialized`, `Bot._requests_initialized`, `Bot._request` (getUpdates + API), `Updater._initialized`. Public `Bot.request` — только API-клиент. `SimpleUpdateProcessor.shutdown` no-op и **не** считается очисткой.

---

## Success Criteria

- [x] Helper: initialize → start → wait stop → stop → shutdown; cleanup при ошибке и отмене
- [x] `enable_polling=True` → `ValueError` до initialize; httpx от `build()` остаются на caller
- [x] Нет `sys.exit`/`os._exit`/закрытия чужого loop в helper
- [x] Primary ошибка сохраняется; cleanup-only error не возвращает успех
- [x] Sandbox: реальная сборка Antares, local snapshot, реальный Application и handlers, синтетический `do_request` только getMe/sendMessage
- [x] `/whoami` через `update_queue`; наблюдаемый callback и sendMessage; затем stop
- [x] Ошибка callback → error handler с `RuntimeError` / `injected whoami callback failure`; нет `whoami_completed`
- [x] Стадии: requests, get_me, after Bot / before Application flag, start, cancel, cleanup-only
- [x] Jobs/sender/worker/Dropbox/PG/браузер/живая сеть запрещены harness; `start_polling`/`run_polling` blocked
- [ ] GPT review
- [ ] merge/deploy (намеренно открыто)

---

## Реальные вызовы vs заглушки

| Реально | Заглушка / отказ |
|---------|------------------|
| `assemble_antares`, local xlsx snapshot, `Application.builder().token().concurrent_updates(True).build()`, те же handlers | live Telegram / getUpdates / сеть |
| PTB `initialize`/`start`/`stop`/`shutdown`, `update_queue`, `_update_fetcher` | `HTTPXRequest.do_request`: JSON только для getMe и sendMessage |
| production `run_ptb_lifecycle` | инъекции отказов только в sitecustomize harness |
| `/whoami` (`cmd_whoami`) | `/run_*`, jobs, sender, worker, Dropbox, PG, Playwright |

---

## Прогоны (Cursor, Python 3.12.10, PTB 22.8, httpx 0.28.1)

job-queue extra: **нет** (`APS_AVAILABLE is False`). Ветки JobQueue extra **не** заявляются проверенными. `requirements.txt` не менялся.

```
py -3.12 -m pytest tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py -q --tb=short
```

**91 passed, 0 failed, 0 skipped, exit 0.**

Только lifecycle:

```
py -3.12 -m pytest tests/unit/test_antares_lifecycle.py -q --tb=short
```

**10 passed, 0 failed, 0 skipped, exit 0.**

### Таблица очистки (факт subprocess)

| Стадия | Что было | Cleanup helper | Leftover | Код |
|--------|----------|----------------|----------|-----|
| `enable_polling` | только `build()` | нет (отказ до initialize) | `*.httpx_open` (caller) | 2 ValueError |
| `fail_requests` | requests flag ложь | `get_updates_request.shutdown`, `request.shutdown` (не `Bot.shutdown`) | нет | 2 RuntimeError |
| `fail_requests_and_cleanup` | как выше | shutdown инъекция | `*.httpx_open` | 2 RuntimeError, `__cause__` ExceptionGroup |
| `fail_get_me` | requests да; app нет | `bot.shutdown` | нет | 2 InvalidToken |
| `fail_after_bot` | Bot.initialize да; processor отказ; app flag ложь | `bot.shutdown`; processor no-op не считался | нет | 2 RuntimeError |
| `fail_start` | app initialized; running нет | `app.shutdown` (без `app.stop`) | нет | 2 RuntimeError |
| `whoami` | start без polling | `app.stop`, `app.shutdown` | нет; fetcher done | 0 |
| `callback_error` | start; error handler | `app.stop`, `app.shutdown` | нет | 0 helper; нет `whoami_completed` |
| `cancel` | start | `app.stop`, `app.shutdown` | нет; fetcher done | 2 CancelledError |
| `fail_cleanup_only` | whoami + stop | `app.shutdown` затем инъекция | нет | 2 RuntimeError; success diag нет |

GPT этот набор **не** запускал.

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | sandbox lifecycle + прежний boot/run + parser/gate |
| GPT | ещё не ревьюил |

---

## Out Of Scope

live polling; SIGINT/SIGTERM; drain хвоста очереди; публичный serve; `enable_polling=True` реализация; JobQueue extra; Railway; merge; исходное Test; смена boot/run.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | helper + sandbox subprocess; Draft PR #27; к review |
