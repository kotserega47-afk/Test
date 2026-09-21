# Контракт Application без запуска (TASK-21)

| Мета | Значение |
|------|----------|
| **Статус** | PROPOSED (только документы; runtime не менялся; к review, не «review пройден») |
| **База** | закрытие TASK-20 `5bed1b0308775a769481409e18e20644b181967d` (принятый review HEAD `8b42e4daadbdcdd71a0843170d859ea98d190208`) |
| **Lifecycle** | [MODULAR_REORG_ANTARES_LIFECYCLE.md](MODULAR_REORG_ANTARES_LIFECYCLE.md) шаг 2 после локального snapshot |
| **PTB (обследован)** | `python-telegram-bot` **22.8** (Cursor, Python 3.12.10: `telegram.__version__`); `requirements.txt` закрепляет `>=20.7`, не pin 22.8 |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Это обследование **исходников PTB 22.8** в site-packages Python 3.12.10 и **текущего** isolated/mixed кода на SHA закрытия TASK-20. Живой Telegram, рабочий token и Railway env **не** вызывались. Неизвестное — **UNKNOWN**. Runtime, mixed gate и `apps/antares.py` в этом PR **не** менять.

`Application.build()` **не** есть запуск бота и **не** есть успешная авторизация token.

---

## 1. Источники

| Файл (PTB 22.8) | Что закреплено |
|-----------------|----------------|
| `telegram/ext/_applicationbuilder.py` | `token()` только сохраняет строку; `build()` собирает `ExtBot` + два `HTTPXRequest` + `Updater` + default `JobQueue`; **не** вызывает `initialize` / `start` / polling |
| `telegram/request/_httpxrequest.py` | `httpx.AsyncClient` создаётся в `__init__` (`_build_client`); HTTP к Telegram в `do_request`; `shutdown()` → `aclose()` |
| `telegram/_bot.py` | `initialize()` → `BaseRequest.initialize` + **`get_me`** (проверка token сетью); `shutdown()` **no-op**, если `_requests_initialized` ложно |
| `telegram/ext/_application.py` | `initialize` / `start` / `stop` / `shutdown` / `add_handler` / `run_polling`; `shutdown()` **no-op**, если не `_initialized` |
| `telegram/ext/_jobqueue.py` | `JobQueue()` создаёт `AsyncIOScheduler`; `.start()` только из `Application.start()` |
| `scheduler.py` L353–365 | mixed: `Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()`; затем `add_handler`; snapshot **после** Application; `run_polling` |
| `apps/antares.py` | boot/run TASK-20: Application **нет** |
| `modules/antares/handlers.py` `get_antares_handlers` | 17 `CommandHandler` + `MessageHandler(filters.Document.ALL, …)` |
| `tests/fixtures/behavior_baseline/expected_antares_tg_commands.json` | порядок имён команд |
| `modules/antares/assembly.py` | один вызов `get_antares_handlers()` → `assembled.handlers` |

Версия Railway / Nixpacks **UNKNOWN**. Контракт code PR — API 22.8 + та же цепочка builder, что mixed. Если установленный PTB без extra `job-queue`, `ApplicationBuilder` ставит `job_queue=None` (`RuntimeError` «PTB must be installed via…»). Cursor 3.12.10 extra есть (`JobQueue()` в builder не падает).

---

## 2. Точный API построения

Синхронно, как mixed:

```text
Application.builder().token(token).concurrent_updates(True).build()
```

- `token` — тот же `(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()`, уже проверенный boot prefix. Пустой token до builder не доходит.
- `token()` **не** ходит в сеть; без token `build()` → `RuntimeError("No bot token was set.")`.
- `concurrent_updates(True)` → `SimpleUpdateProcessor(256)` (`_applicationbuilder.py` ~1071–1080).
- Default `job_queue=JobQueue()` (если extra установлен); `build()` вызывает `job_queue.set_application(application)`.
- Default persistence **нет**.
- Default updater: `Updater(bot=bot, update_queue=Queue())`.
- `build()` **не** вызывает `Application.initialize`, `Bot.initialize`, `get_me`, `Updater.start_polling`, `Application.start`, `run_polling`.

Не считать успех `build()` авторизацией token. Проверка token — `Bot.initialize` → `get_me` (сеть). Это **вне** TASK-21 code.

---

## 3. Ресурсы `build()` и когда появляется сеть

| Объект после `build()` | Сеть / I/O |
|------------------------|------------|
| `ExtBot` + два `HTTPXRequest` (общий request pool 256, getUpdates pool 1) | Каждый request **конструирует** `httpx.AsyncClient`. Соединение к `api.telegram.org` **не** открывается, пока нет `do_request` / `get_me`. |
| `Updater` + `asyncio.Queue` | локально |
| `JobQueue` + `AsyncIOScheduler` | локально; scheduler **не** `start()` |
| `SimpleUpdateProcessor` | локально |
| handlers на Application | пусто, пока нет `add_handler` |

**Сеть Telegram HTTP** начинается на `Application.initialize` → `Bot.initialize` → `get_me`. `run_polling` начинает с `initialize`, затем `Updater.start_polling` (getUpdates).

Idle `httpx.AsyncClient` после `build()` — ресурс процесса, не сессия Telegram. Subprocess-тесты **запрещают** внешнюю сеть (`socket.create_connection` и границы SDK, как TASK-20). `build()` при этом допускается: клиент создаётся без `create_connection`. Если в конкретной среде `httpx` всё же трогает сокет на construct — это **UNKNOWN до code PR**; тогда фиксировать наблюдение, не ослаблять запрет сети.

---

## 4. Границы методов

| Метод | Что делает | В будущем code TASK-21 |
|-------|------------|-------------------------|
| `Application.build()` | объекты в памяти | **да**, после успешного локального snapshot |
| `add_handler` / цикл как mixed | список handlers; `TypeError`, если не `BaseHandler` | **да**, объекты из `assembled.handlers` |
| `Application.initialize` / `Bot.initialize` / `get_me` | сеть + проверка token | **нет** |
| `Application.start` | fetcher + **`JobQueue.start()`** | **нет** |
| `Updater.start_polling` / `run_polling` | initialize + polling + start | **нет** |
| `Application.stop` / `Updater.stop` | только после start/polling | **нет** (нечего останавливать) |
| `Application.shutdown` | `bot.shutdown` и т.д. **только если `_initialized`** | **не вызывать как cleanup** после одного `build()` |
| `Bot.shutdown` | `HTTPXRequest.shutdown` **только если `_requests_initialized`** | то же |
| процесс exit | ОС забирает fd/клиенты | **да** — завершение после диагностики |

`run_polling` порядок (докстринг `_application.py` ~758–769): `initialize` → `post_init` → `Updater.start_polling` → `start` → … → `Updater.stop` → `stop` → `post_stop` → `shutdown`. Isolated этот путь **не** входит.

---

## 5. Освобождение после одного `build()` (обоснование)

Lifecycle TASK-19 писал: «shutdown если initialize был». На PTB 22.8:

- `Application.shutdown` (`_application.py` 539–541): если не `_initialized`, **сразу return**.
- `Bot.shutdown` (`_bot.py` 878–880): если не `_requests_initialized`, **сразу return**.
- `_initialized` / `_requests_initialized` ставятся только в `initialize()`, а `Bot.initialize` вызывает **`get_me`**.

Поэтому **нельзя** обещать `Application.shutdown()` / `Bot.shutdown()` как закрытие httpx после build-only: официальный путь cleanup **требует** initialize, а initialize — сеть и проверка token.

`HTTPXRequest.shutdown()` (`_httpxrequest.py` 234–240) **умеет** `aclose()` без initialize. Это **не** публичный контракт Application. Вызывать `app.bot.request.shutdown()` в этом срезе **не** требуется и **не** входит в минимальный code: нет гарантии стабильного атрибута на всех `>=20.7`; это обход PTB lifecycle.

**Контракт освобождения для build-only:** не `initialize` ради shutdown; не `Application.shutdown()` как обязательный шаг; процесс **завершается** после диагностики (как boot/run TASK-18/20). Предупреждение httpx «unclosed client» при GC — **UNKNOWN**; не делать из него success criterion; не запускать event loop только чтобы `aclose`.

Иной порядок (прямой `HTTPXRequest.aclose`) — отдельное решение **после** исходников будущей версии, не этот docs PR и не первый application code.

---

## 6. Частичная ошибка `build` / `add_handler`

| Отказ | Уже есть | Действие |
|-------|----------|----------|
| gate / token / assemble | как TASK-20 | Application **не** строить |
| нет/не файл xlsx / snapshot / publish | сборка | Application **не** строить; нет успешной диагностики |
| `build()` исключение | snapshot ok; Application может отсутствовать | exit ≠ 0; нет polling; нет success line |
| N handlers добавлены, N+1 `add_handler` падает (`TypeError` не-`BaseHandler` или иное) | Application + частичный `app.handlers` | exit ≠ 0; **не** initialize/start/polling; **не** success line; процесс exit (не `Application.shutdown`) |
| успех build + все handlers | объекты в памяти | одна успешная диагностика **в конце**; процесс завершается |

Не чинить mixed `main()`. Mixed по-прежнему строит Application **до** snapshot.

---

## 7. Минимальный будущий code scope (не этот PR)

Только `apps/antares.py` `run` после успешного `_run_local_rules` / snapshot (без печати `_RUN_OK` до Application):

1. Boot без argv / `boot` — **как TASK-18**: сборка, `antares boot ok`, workbook не читается, Application нет.
2. `run`: prefix сборки → локальный `.xlsx` → `get_snapshot(force_sync=True)` → **затем** `Application.builder().token(token).concurrent_updates(True).build()` → `add_handler` для **тех же экземпляров** `assembled.handlers` (не второй `get_antares_handlers()`).
3. Состав: 17 команд в порядке `expected_antares_tg_commands.json` + document; identity `handler.callback` как у `assembled.handlers` / `cmd_*` и `handle_wallet_editor_document`.
4. Сбой любого шага → exit ≠ 0; строка успеха **только** если всё прошло.
5. Нет `initialize` / `start` / `run_polling` / `Updater.start_polling`; нет sender, worker bootstrap, `schedule_loop`.
6. Процесс завершается после диагностики (нет idle).

Не менять: `core/access_rules.py`, `rules_provider`, mixed gate, `scheduler.py`, Railway, token live-проверку.

Диагностика успеха `run` — **одна**, после handlers (например расширить текущий `_RUN_OK`, не печатать `antares run ok local_rules` до Application: иначе сбой build оставит ложный success). Точная строка — code PR; тесты проверяют отсутствие success на отказе и наличие в конце на успехе.

Token в тестах — **не** рабочий. Для `build()` достаточно непустой строки формата PTB; `get_me` не вызывается.

---

## 8. Контракт subprocess-проверок (не запускать в TASK-21)

Как TASK-20: отдельный процесс, sandbox, реальные `assemble_antares` / `AccessRules` / snapshot, запрет сети и SDK на границе. **Дополнительно:** реальный `telegram.ext.Application.build()` и реальные handlers из сборки. Не stub'ить `telegram.ext` целиком (в отличие от registration/legacy harness).

Не класть `integrations.telegram_bot` / `tg_commands` / raccoon в `sys.modules` заранее. Загрузка = провал.

Обернуть `Application.initialize`, `Application.start`, `Application.run_polling` (и при наличии `Updater.start_polling`) — запись события, если вызваны; успех build-only = **ноль** таких вызовов.

Живой Telegram и prod token **запрещены**.

| # | Сценарий | Ожидание |
|---|----------|----------|
| B | boot без argv | TASK-18: exit 0; нет snapshot; нет Application.build; нет sender/mixed/raccoon |
| F1 | `run`, отказ сборки (foreign key / bind) | snapshot нет; **Application не строится**; exit ≠ 0 |
| F2 | `run`, отказ пути/snapshot/publish | **Application не строится**; нет success |
| S1 | `run` + валидный sandbox xlsx | assemble → snapshot `force_sync=True` → реальный `build()` → handlers; состав 17+document, порядок и identity callback; одна success в конце; exit 0; процесс не живёт |
| E1 | ошибка `build()` | нет success; процесс завершается ≠ 0; нет initialize/start/polling |
| E2 | ошибка `add_handler` после частичного списка | нет success; процесс завершается ≠ 0; нет initialize/start/polling |
| N1 | любой `run` | `initialize` / `start` / `run_polling` **не** вызывались |
| I1 | sender / mixed / raccoon | не импортировались |
| G1 | mixed `scheduler.py` + `PROJECT_PROFILE=antares` | exit 2 без изменений |

Pytest в этом docs PR **не** требуется и **не** запускался.

---

## 9. Out of scope

`initialize` / polling / worker / schedules / sender; прямой `HTTPXRequest.shutdown`; смена `requirements.txt`; mixed `main()`; `JOB_ACCEPT`; Railway; cutover; merge/retarget/deploy; исходное дерево Test; живой token.

---

## 10. Оставшиеся вопросы

1. Совпадает ли PTB на Railway с 22.8, или только `>=20.7` — **UNKNOWN**.
2. Будет ли httpx warning unclosed client при exit после `build()` — **UNKNOWN** до code.
3. Нужен ли когда-либо прямой `HTTPXRequest.aclose` без initialize — **не в этом срезе**; источники не дают Application API.
4. Точный текст success-строки `run` после Application — code PR (правило: одна строка, только в конце).
5. Remote workbook / stale reuse перед сервисом — по-прежнему отдельное решение (LIFECYCLE § 4.3).
