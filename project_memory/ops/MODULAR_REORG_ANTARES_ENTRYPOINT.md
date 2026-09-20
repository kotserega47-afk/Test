# План isolated Antares entrypoint (TASK-17)

| Мета | Значение |
|------|----------|
| **Статус** | PROPOSED (только документы; runtime не менялся) |
| **База** | закрытие TASK-16 `950a66a745bbac64cbf2281951a5ed28e01474cf` (review HEAD `f29cc89…`) |
| **Уточнение контракта** | после review HEAD `40d84b262c5bb93450c288db86aa1cb049b14cab` |
| **Сборка** | [MODULAR_REORG_ANTARES_ASSEMBLY.md](MODULAR_REORG_ANTARES_ASSEMBLY.md) |
| **ADR** | [MODULAR_REORG_ADR.md](MODULAR_REORG_ADR.md) `apps/` |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Этот документ — контракт **отдельного** процесса Antares. Mixed `scheduler.py` и `enforce_legacy_scheduler_profile()` остаются как есть: явный `PROJECT_PROFILE=antares` на mixed entry — отказ (exit 2). Isolated entry **не** требует и **не** предполагает последующего ослабления mixed gate: это другой файл запуска. Railway `startCommand`, профили production, merge и cutover **не** входят.

---

## 1. Путь, пакет `apps` и команда

| | Контракт | Сейчас (CONFIRMED) |
|--|----------|--------------------|
| Пакет | `apps/` — Python package: `apps/__init__.py` (пустой допустим) + `apps/antares.py` | каталога **нет** |
| Команда | из **корня репозитория**: `py -3.12 -m apps.antares` | не существует |
| `sys.path` | `antares.py` добавляет корень репозитория (`Path(__file__).resolve().parent.parent`) в `sys.path` **сам**, без пользовательского `PYTHONPATH` | — |
| Mixed | `python scheduler.py` | L368–369 |
| Prod | `/usr/bin/tini -s -- /opt/venv/bin/python scheduler.py` | `railway.toml` |

Не считать командой `python apps/antares.py`: тогда `sys.path[0]` = `apps/`, `import core` ломается. `-m apps.antares` из корня находит пакет `apps`; вставка корня из `__file__` даёт `core` / `modules` / `integrations`.

Первый code PR **не** меняет `railway.toml`, `nixpacks.toml`, `Procfile`. Не вызывать isolated через `import scheduler` / `python scheduler.py`.

---

## 2. Профиль: окружение процесса, затем dotenv

Парсер `parse_project_profile` **не** менять. Isolated gate — **новая** функция; `decide_legacy_scheduler_boot` / `enforce_legacy_scheduler_profile` **без** смены поведения.

**Порядок:** прочитать `PROJECT_PROFILE` из `os.environ` процесса (**до** `load_dotenv`). Нормализация — существующий parser: отсутствует ключ → `None` → `implicit_default=True`; `""` / whitespace → strip → `implicit_default=True`; точное `antares`/`raccoon`/`wr` → `implicit_default=False`; иное → `InvalidProjectProfileError`.

Профиль, который есть **только** в `.env`, **не** даёт допуск (dotenv ещё не читали).

| Вход процесса (до dotenv) | Isolated `-m apps.antares` | Mixed `scheduler.py` |
|---------------------------|----------------------------|----------------------|
| точное `PROJECT_PROFILE=antares` | допуск | exit **2** (без изменений) |
| ключ отсутствует / `""` / whitespace | отказ exit 2 (это mixed, не Antares) | `legacy_mixed` |
| `raccoon` / `wr` | отказ exit 2 | отказ exit 2 |
| неизвестное значение | отказ exit 2 | отказ exit 2 |

Отказ gate: stderr + exit 2 **до** dotenv, token, `assemble_antares`, `telegram_bot`, worker, schedules.

### `.env`

| Правило | Значение |
|---------|----------|
| Путь | только `{repo_root}/.env`, где `repo_root = Path(__file__).resolve().parent.parent` для `apps/antares.py` |
| Вызов | `load_dotenv(dotenv_path=repo_root / ".env", override=False)` |
| Поиск | **не** вызывать `find_dotenv()`, **не** обходить родительские каталоги |
| Файл отсутствует | допустимо (Railway env); загрузка no-op |
| Тесты | временный `.env` в sandbox; **без** рабочих credentials / боевого token |

Token **после** этой загрузки: `(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()`; whitespace = пусто → отказ **до** import `telegram_bot`. Не использовать `TG_BOT_TOKEN`.

---

## 3. Boot: единственное поведение

Первый code PR реализует **только** шаги 1–6. Idle-режима, polling и фонового ожидания **нет**.

1. Isolated gate по `os.environ` (до dotenv).
2. `load_dotenv` по § 2.
3. Token strip; пусто → exit ≠ 0.
4. `AccessRules(RULES_XLSX_PATH)` + MAIN logger (конструктор workbook не читает).
5. `assemble_antares(rules=…, logger=…)`.
6. **Успех:** краткий диагностический вывод (профиль, факт сборки, число handlers, семь keys) → **`SystemExit` / exit 0**. Процесс **завершается**. Нет `run_polling`, worker, `schedule_loop`, idle sleep.

Сбой gate / token / `AntaresAssemblyError` / сбой импорта → exit ≠ 0, без шагов 6 (успех) и без старта компонентов.

Поздние шаги (**не** boot-PR): fail-fast snapshot; Application + handlers; worker; filtered schedules; polling.

Mixed `main()` (CONFIRMED L349–365) не копировать.

---

## 4. Будущий старт и остановка (после boot-PR)

Старт: gate + dotenv + token → rules/logger → assembly → snapshot → Application/handlers → worker → filtered schedule thread → polling.

Остановка (сейчас в mixed нет graceful shutdown): stop polling → schedule thread → jobs → worker queues → sender loop. Stop API у sender **нет**. Boot-PR shutdown не вводит.

---

## 5. Транзитивный import sender (CONFIRMED на текущем коде)

`assemble_antares` вызывает `_check_job_registry_preconditions` → `antares_job_executors()` и затем `register_jobs` → снова `antares_job_executors()`. Это **единственный** путь регистрации шести Antares jobs. Тесты TASK-16 со **stub** `telegram_bot` в sitecustomize **не** доказывают отсутствие sender в настоящем boot: stub как раз подменяет модуль и скрывает import-time loop.

### 5.1 Цепочка wallet (как в review)

```
assemble_antares
  → antares_job_executors
    → integrations.downloader_wallets   # module-level
      → transport.telegram_transport    # L6: from integrations.telegram_bot import …
        → integrations.telegram_bot     # load_dotenv + daemon loop на import
```

`downloader_wallets` также импортирует `telegram_routes` на уровне модуля; `send_message_to_route` / `send_file_to_route` делают **lazy** import `telegram_bot` внутри функций — этот путь **сам** sender на assemble не стартует.

### 5.2 Остальные шесть keys при `antares_job_executors()`

| Key | Как берётся callable | Import `telegram_bot` на **вызове** `antares_job_executors()` |
|-----|----------------------|---------------------------------------------------------------|
| `wallet` | `from integrations.downloader_wallets import run_wallet_cycle` | **да** — § 5.1 |
| `rate` | `from integrations.bakai_monitor_playwright import run_rate_monitor_safe` | **да** — L11 `from integrations.telegram_bot import send_message_sync` |
| `wallet_editor_registry_replay` | `from integrations.wallet_editor_registry import run_registry_outbox_replay_job` | **да** — L24 тот же прямой import |
| `wallet_editor_registry_refresh` | `from integrations.wallet_editor_registry_refresh import …` | **да** — L18 прямой import **и** L19 import `wallet_editor_registry` (§ выше) |
| `hourly` | `run_hourly_job` (определён в `jobs.py`) | **нет** на assemble: `hourly_report` / `telegram_routes` только внутри функции при **запуске** job |
| `download` | `run_download_job` (обёртка в `jobs.py`) | **нет** на assemble: `integrations.downloader` (L23 прямой `telegram_bot`) только при **запуске** job |

Итого на текущем коде **нельзя** обещать boot без sender, если только не размыкать границы ниже. `register_script_job("operator_wallets_ready")` runtime/telegram **не** импортирует (lazy в executor). `handlers.get_antares_handlers` не импортирует `telegram_bot`; `cmd_status` — lazy при `/status` (boot `/status` не вызывает).

### 5.3 Необходимое размыкание в boot-PR (минимальное)

Перенести `from integrations.telegram_bot import …` **внутрь** функций отправки, **без** смены сигнатур, маршрутов и **без** переделки lifecycle sender (loop по-прежнему стартует при первом реальном import модуля, не на assemble):

1. `transport/telegram_transport.py` — сейчас module-level `send_file_sync` / `send_message_sync`; перенести внутрь `send_text` / `send_document`.
2. `integrations/bakai_monitor_playwright.py` — module-level `send_message_sync` → внутрь функций, которые вызывают send.
3. `integrations/wallet_editor_registry.py` — то же.
4. `integrations/wallet_editor_registry_refresh.py` — то же (даже после lazy в registry: у refresh свой прямой import).

Не входит в это размыкание (уже lazy или не на assemble): `telegram_routes` send helpers; `modules.antares.handlers.cmd_status`; `jobs.run_hourly_job` / `run_download_job`; `script_jobs.bind` executor.

`integrations/downloader.py` L23 остаётся module-level; на boot assemble **не** грузится. Менять в boot-PR **не** обязательно; если позже `antares_job_executors` начнёт eager-import `downloader` — отдельная граница.

Более широкое изменение (не обещать вместо п. 1–4): отложить сам `antares_job_executors()` / не импортировать downloader-модули до первого job; split `job_runner`; отложенный Dropbox. Без п. 1–4 boot без sender на **текущем** коде **невыполним**.

### 5.4 Прочие клиенты на assemble (не sender)

- `job_runner` → `rules_provider` → import `dropbox_watcher`; `_get_dbx()` lazy при download.
- `downloader_wallets` / `bakai_monitor_playwright` / registry refresh импортируют `playwright.sync_api` на уровне модуля — браузер не стартует от import. Live Playwright/Dropbox/PG/Telegram API в boot-тестах блокировать на границах (не подменять `telegram_bot` stub'ом в `sys.modules`).

---

## 6. Минимальный TASK-18 code scope (не начинать в TASK-17)

Один boot-PR:

- пакет `apps/` + `apps/antares.py`; команда `py -3.12 -m apps.antares`
- isolated gate (mixed без изменений)
- dotenv § 2; token после dotenv
- AccessRules + logger + `assemble_antares`
- lazy-import sender на границах § 5.3
- успех → диагностика → **exit 0**
- subprocess-harness § 7

Не включать: polling, worker, schedules, Railway, `JOB_ACCEPT`, cutover, split `job_runner`, отдельный `STATE_DIR`, shutdown sender, ослабление mixed gate, idle.

---

## 7. Subprocess-проверки boot-PR

Отдельный процесс. Pytest родителя не обходит gate. **Не** класть `telegram_bot` (ни stub, ни настоящий) в `sys.modules` **заранее**.

Наблюдаемый запрет импорта: meta path / hook, который при **любой** попытке загрузить `integrations.telegram_bot` (и аналогично `integrations.tg_commands`, `integrations.raccoon_jobs`, raccoon downloaders) **валит проверку**. Это не stub, который притворяется модулем.

Реальные: isolated gate, `assemble_antares`, `register_jobs`, `register_script_job`, `core.job_runner.JOB_REGISTRY`. Dropbox download, Playwright run, живой Telegram API, PG — отказать на границах вызова, не подменой `telegram_bot`.

| # | Проверка | Ожидание |
|---|----------|----------|
| 1 | профиль unset / `""` / whitespace / raccoon / wr / мусор | exit 2; `telegram_bot` **не** загружался |
| 2 | `antares` в процессе; профиль только в `.env` sandbox | отказ (gate до dotenv) |
| 3 | `antares`, token whitespace/пусто после dotenv | отказ; `telegram_bot` не загружался |
| 4 | успех сборки | семь keys; диагностика; **exit 0**; процесс не живёт; `telegram_bot` / mixed / raccoon **не** в `sys.modules` и hook не срабатывал |
| 5 | чужой ключ / конфликт bind | `AntaresAssemblyError`, exit ≠ 0; снова **нет** sender/mixed/raccoon import (мало «`run_polling` не вызван») |
| 6 | mixed `scheduler.py` + `PROJECT_PROFILE=antares` | по-прежнему exit 2 |

Временный `.env` без рабочих секретов. Docs-only TASK-17: pytest **не** запускать.

---

## 8. Блокеры и UNKNOWN

Не блокеры review **этого плана**.

Boot-PR **выполним**, если в него входит размыкание § 5.3. Иначе обещание «assemble без sender» ложно.

Блокеры **позднего start** (не boot): фильтр schedules; два процесса / один token / общий `STATE_DIR`; Railway всё ещё `scheduler.py`; нет stop API sender.

UNKNOWN (без mixed gate):

- U12: raccoon schedules на сервисе Test.
- Нужен ли отдельный `STATE_DIR` при первом start-PR или только cutover.

**Не UNKNOWN:** нужен ли ослабленный mixed gate для isolated entry — **нет, не нужен**. Isolated = `-m apps.antares` + `PROJECT_PROFILE=antares` в **окружении процесса**. Mixed продолжает отклонять явный `antares`.
