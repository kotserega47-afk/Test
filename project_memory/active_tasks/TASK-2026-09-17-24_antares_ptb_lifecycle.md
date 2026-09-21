# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-24 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-23 (PR #26, review `6540a36ebe19408e76e8e8b9f1cbc20116c64d97`, закрытие `94951c6ff4b3e9137f81151fac81e6fe2739e77a`), `modules/antares/application_lifecycle.py` |
| **PR** | Draft [#27](https://github.com/deniskotdavydov1991-wq/Test/pull/27) `feat/task-2026-09-17-24-antares-ptb-lifecycle`, base `feat/task-2026-09-17-23-antares-startstop` |
| **Риск** | medium: реальный PTB initialize/start/stop/shutdown в sandbox без live Telegram |

Production helper `run_ptb_lifecycle` поддерживает только Application с `SimpleUpdateProcessor`, присутствующим Updater, без persistence и без JobQueue extra. Иное отклоняется `ValueError` до initialize. JobQueue extra **не** проверен.

Если `Application.shutdown` падает, helper **не** повторяет его целиком: закрывает оставшиеся running-флаги и открытые HTTPX-клиенты по отдельности. Ошибка одного `request.shutdown` не отменяет второй. Cleanup исполняется в shielded-задаче; отмена во время cleanup не пропускает шаги, caller видит `CancelledError`.

Снимки ресурсов в sandbox пишутся **внутри** loop до `asyncio.run`.

Review **пройден** на HEAD `34ef7af…`. GPT читал diff/контракт, наборы **не** запускал. Cursor: **15 / 96 passed** (Python 3.12.10, PTB 22.8, httpx 0.28.1). Поддержка helper: SimpleUpdateProcessor, Updater, без persistence и JobQueue extra. Boot/`run` helper **не** вызывают. Serve/polling **нет**. Lifecycle **не выпущен**. Полноценный stop бизнес-потоков **не** готов. PR #27 остаётся Draft. Допуск работы — TASK-25.

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
- [x] `Application.shutdown` до реальной очистки: fallback закрывает HTTPX; primary виден
- [x] Один `request.shutdown` падает — второй всё равно закрывается
- [x] Состояние ресурсов снято внутри loop до `asyncio.run`
- [x] Отмена во время cleanup не пропускает remaining steps; CancelledError виден caller
- [x] Неподдерживаемый JobQueue/processor — отказ до initialize
- [x] GPT review HEAD `34ef7af…`: код/diff; 15/96 Cursor; GPT наборы не запускал; не выпущено
- [ ] merge/deploy (намеренно открыто)
- [ ] TASK-25 контракт допуска работы (отдельное задание)

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

Исторические границы:
- `e737281` / `e039e25`: **91 / 10** passed
- `d842912`: **94 / 13** passed

Текущий lifecycle:

```
py -3.12 -m pytest tests/unit/test_antares_lifecycle.py -q --tb=short
```

**15 passed, 0 failed, 0 skipped, exit 0.**

Regression:

```
py -3.12 -m pytest tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py tests/unit/test_project_profile.py tests/unit/test_project_profile_boot.py -q --tb=short
```

**96 passed, 0 failed, 0 skipped, exit 0.**

### Таблица очистки (факт subprocess, in-loop до выхода из asyncio.run)

| Стадия | Primary | Cleanup | in-loop HTTPX / running | Код |
|--------|---------|---------|-------------------------|-----|
| `enable_polling` | ValueError до initialize | нет | клиенты открыты (caller) | 2 |
| `unsupported_job_queue` / `unsupported_processor` | ValueError до initialize | нет | caller | 2 |
| `fail_requests` | RuntimeError initialize | оба `*.shutdown` | `[True, True]` | 2 |
| `fail_requests_and_cleanup` | RuntimeError initialize; cleanup ExceptionGroup | первый shutdown fail, второй ok | `[False, True]` | 2 |
| `fail_get_me` | InvalidToken | `bot.shutdown` | `[True, True]` | 2 |
| `fail_after_bot` | RuntimeError processor | `bot.shutdown` | `[True, True]` | 2 |
| `fail_start` | RuntimeError start | `app.shutdown` | `[True, True]` | 2 |
| `whoami` | нет | `app.stop`, `app.shutdown` | закрыты; fetcher done | 0 |
| `callback_error` | нет (helper) | `app.stop`, `app.shutdown` | закрыты | 0; error handler |
| `cancel` | CancelledError | `app.stop`, `app.shutdown` | закрыты; fetcher done | 2 |
| `cancel_during_cleanup` | CancelledError (`cancelled_during_cleanup`) | `app.stop` и `app.shutdown` не пропущены | закрыты | 2 |
| `double_cancel_cleanup` | CancelledError; два cancel на одном cleanup | один `_cleanup_application`; stop/shutdown после hold | `[True, True]`; fetcher/cleanup done | 2 |
| `cancel_during_initialize_cleanup` | initialize RuntimeError + cancel | ExceptionGroup shutdown; cancelled_during_cleanup | `[False, True]` | 2 |
| `fail_cleanup_only` | RuntimeError shutdown до orig | fallback `get_updates_request.shutdown` + `request.shutdown` | `[True, True]` | 2 |

Поддерживаемый состав: `SimpleUpdateProcessor`, Updater есть, persistence нет, JobQueue extra нет. Иное — явный отказ. Extra **не** проверен.

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
| Cursor | 15 passed lifecycle + 96 с boot/parser/gate на HEAD `34ef7af…` (Python 3.12.10, PTB 22.8, httpx 0.28.1) |
| GPT | Код/diff HEAD `34ef7af…`; тесты не запускал |

---

## Out Of Scope

live polling; SIGINT/SIGTERM; drain хвоста очереди; публичный serve; `enable_polling=True` реализация; JobQueue extra; Railway; merge; исходное Test; смена boot/run.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | helper + sandbox subprocess; Draft PR #27; к review |
| 2026-09-21 | shield удерживается до конца одной cleanup-задачи; double-cancel и cancel во время initialize-cleanup |
| 2026-09-21 | GPT review HEAD `34ef7af…`: 15/96 Cursor; GPT наборы не запускал; не выпущено; stop бизнес-потоков не готов |
