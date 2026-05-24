# Decisions — `analizis`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Последнее обновление** | 2026-05-24 |
| **Метод** | Stage A4 — extraction из KB, кода, repo-документов |
| **Связанные документы** | `architecture_map.md`, `contracts.md`, `current_state.md` |

> Решения восстановлены постфактум из кода и документов. **Не выдумывать** — неподтверждённые помечены **UNKNOWN**.

**Статусы записи:** CONFIRMED | DORMANT | UNKNOWN | DOCS_ONLY | STALE_RISK

---

## Как читать этот журнал

| Раздел | Содержание |
|--------|------------|
| **Active Decisions** | Реализовано и участвует в production path (`scheduler.py` → …) |
| **Dormant Decisions** | Было задумано/частично реализовано, но не в prod entry или пустой код |
| **Questionable Decisions** | Реализовано, но с противоречиями, рисками или неясным статусом |
| **Documentation-only Decisions** | Зафиксировано в документах, в runtime не подтверждено |

Перекрёстные ID: **E#** — explicit architectural decision; **I#** — implicit invariant из кода.

---

## Active Decisions

### E1 — Railway как production platform

| Поле | Значение |
|------|----------|
| **Decision ID** | E1 |
| **Status** | CONFIRMED |

**Context**  
Нужен managed-хостинг для одного long-running Python-процесса с Playwright (Chromium) и периодическим рестартом.

**Decision**  
Деплой на **Railway** через Nixpacks: сервис `file-analyzer`, builder NIXPACKS, venv `/opt/venv`.

**Alternatives**  
Не описаны в репозитории. `Procfile` содержит только `postinstall` (apt + playwright), не альтернативный process type — **UNKNOWN** был ли Heroku-стиль ранее.

**Consequences**  
Единый контейнер; рестарт через `os._exit(1)` полагается на перезапуск платформой (комментарий в коде).

**Risks**  
Привязка к Railway semantics; free plan и лимиты — **UNKNOWN** из кода.

**Evidence**  
`railway.toml`, `nixpacks.toml`, `scheduler.py` (комментарий «Railway перезапустит контейнер»).

---

### E2 — `scheduler.py` как единая production entry point

| Поле | Значение |
|------|----------|
| **Decision ID** | E2 |
| **Status** | CONFIRMED |

**Context**  
Нужна одна точка запуска для бота, фоновых циклов Raccoon и политики рестарта.

**Decision**  
Production start: `/opt/venv/bin/python scheduler.py` — единственная команда в `railway.toml`.

**Alternatives**  
Отдельные entry: `main.py`, `integrations/downloader.py`, `downloader_wallets.py` и др. остаются **CLI / manual / UNKNOWN schedule**, не в `railway.toml`.

**Consequences**  
Всё, что не импортируется из `scheduler`, не стартует автоматически с контейнером.

**Risks**  
Название `scheduler.py` вводит в заблуждение (`PROJECT_REFERENCE` называет его «не планировщик») — см. **Q2**.

**Evidence**  
`railway.toml`, `scheduler.py` `if __name__ == "__main__"`, `architecture_map.md` §1.1, `current_state.md` §4.

---

### E3 — Dropbox как хранилище и transport для операционных артефактов

| Поле | Значение |
|------|----------|
| **Decision ID** | E3 |
| **Status** | CONFIRMED |

**Context**  
Нужен общий каталог для входных Excel, архива processed, правил и справочников без деплоя кода.

**Decision**  
**Dropbox API** — канал для: input/processed файлов анализа (`DROPBOX_INPUT_PATH`, `DROPBOX_PROCESSED_PATH`), `rules.xlsx`, `special_cards.xlsx`. Auth: access token **или** refresh token + app key/secret.

**Alternatives**  
Локаль-only, S3, git — не используются в коде для этих артефактов.

**Consequences**  
Сеть и токены Dropbox — критическая зависимость prod; `dropbox_watcher` fail-fast при отсутствии credentials на import.

**Risks**  
Нет retry в wrapper; частичные сбои → False/пустые списки.

**Evidence**  
`integrations/dropbox_watcher.py`, `main.py`, `core/rules_provider.py`, `ARCHITECTURE.md` §2.1 Control Plane storage, `CONTRACT_RULES.md`.

---

### E4 — `rules.xlsx` как хранилище rules engine (Control Plane)

| Поле | Значение |
|------|----------|
| **Decision ID** | E4 |
| **Status** | CONFIRMED |

**Context**  
Бизнес-правила (лимиты, пороги, исключения по времени, ACL бота) должны меняться без релиза кода.

**Decision**  
Единый файл **`rules.xlsx`** в Dropbox (`RULES_XLSX_PATH`), листы: как минимум `access`, `commands`, `exclude_time`, `wallet_limits`, `thresholds_partner` (чтение в коде + спецификация в `CONTRACT_RULES.md`).

**Alternatives**  
`ARCHITECTURE.md` упоминает отдельные `roles.yaml` / `policies.yaml` в repo — в runtime ACL идёт через листы Excel (**реализация отличается от документа** — см. **DOC4**).

**Consequences**  
Операторы и код завязаны на Excel-схему; валидация в `access_rules`, `config_manager`.

**Risks**  
`meta.version` mismatch → отказ по контракту документа, но enforcement в коде — **UNKNOWN** (U9).

**Evidence**  
`core/rules_provider.py`, `core/access_rules.py`, `core/config_manager.py`, `CONTRACT_RULES.md`, `PROJECT_REFERENCE` (rules > yaml > code).

---

### E5 — Telegram как operational interface

| Поле | Значение |
|------|----------|
| **Decision ID** | E5 |
| **Status** | CONFIRMED |

**Context**  
Операторам нужны отчёты, алерты и ручной запуск job без UI/SSH.

**Decision**  
- **Inbound:** long polling (`python-telegram-bot`), команды в `integrations/tg_commands.py`.  
- **Outbound:** `integrations/telegram_bot.py` — очередь async-отправки из sync-кода; **обязательный `chat_id`** на каждый вызов.  
- Разные chat ID per pipeline (`TELEGRAM_CHAT_ID_*`).

**Alternatives**  
Email, web dashboard — не в коде.

**Consequences**  
Все критические уведомления идут в Telegram; каналы разделены по env.

**Risks**  
Два env для токена бота — **Q1**; import `telegram_bot` требует `TELEGRAM_BOT_TOKEN` даже для polling-only сценариев.

**Evidence**  
`scheduler.py`, `telegram_bot.py`, analyzers/downloaders, `PROJECT_REFERENCE`, `current_state.md` ARS-02/07.

---

### E6 — Raccoon workloads в scheduler (primary scheduled architecture)

| Поле | Значение |
|------|----------|
| **Decision ID** | E6 |
| **Status** | CONFIRMED |

**Context**  
Raccoon — основной источник оперативной аналитики (wallet, hourly traffic, daily conversion).

**Decision**  
В `scheduler.main()` три daemon-потока:  
1. `run_raccoon_wallet_cycle` — каждый час в **:00** MSK (`run_hourly_at_minute`, second=5).  
2. `run_hourly_raccoon_cycle` + `run_hourly_report` — каждые `RACCOON_HOURLY_EVERY_MIN` (default **5**).  
3. `run_daily_conversion_loop` — окно **00:00–00:02** MSK, раз в сутки.

**Alternatives**  
Antares hourly (`hourly_downloader` + `hourly_report`) — **не подключены** (DORMANT). Antares full `downloader.py` — вне scheduler (**UNKNOWN** prod).

**Consequences**  
Prod-нагрузка и Playwright-сессии Raccoon доминируют; payin artifact shared: `/tmp/hourly_raccoon/payin.xlsx`.

**Risks**  
Payout download в raccoon wallet **отключён** в коде; daily report зависит от hourly downloader.

**Evidence**  
`scheduler.py`, `integrations/raccoon_*`, `analyzers/raccoon_*`, `architecture_map.md` P4–P6.

---

### E7 — Cache-before-runtime для rules и тяжёлых Excel

| Поле | Значение |
|------|----------|
| **Decision ID** | E7 |
| **Status** | CONFIRMED |

**Context**  
Частые циклы не должны каждый раз тянуть и парсить Dropbox Excel.

**Decision**  
Многоуровневое кэширование:  
- `rules_provider`: `/tmp/rules_cache/rules.xlsx`, TTL `RULES_SYNC_MIN_INTERVAL_SEC` (default 60), ключ `(mtime, size)`, sha256 как `rules_version`.  
- `AccessRules`: in-memory snapshot по `stat_key`.  
- `config_manager`: `_EXCLUDE_TIME_CACHE` по stat_key; notify throttle по hash.  
- Стабилизация файла после download: `_wait_file_stable`.

**Alternatives**  
Всегда fresh download — отвергнуто по TTL/ stat_key в коде.

**Consequences**  
Снижение нагрузки на Dropbox; возможна работа на слегка устаревших rules до TTL.

**Risks**  
Fail-safe отдаёт **последний** snapshot при ошибке sync — намеренно (E8).

**Evidence**  
`core/rules_provider.py`, `core/access_rules.py`, `core/config_manager.py`, `ARCHITECTURE.md` §3.3, `contracts.md` C-01..C-03.

---

### E8 — Fail-safe rules / fail-closed access

| Поле | Значение |
|------|----------|
| **Decision ID** | E8 |
| **Status** | CONFIRMED |

**Context**  
При сбое Dropbox нельзя полностью остановить аналитику, но нельзя пускать неавторизованные команды.

**Decision**  
- **Jobs / rules reads:** при ошибке download — вернуть последний валидный `RulesSnapshot` если файл есть (`rules_provider`).  
- **Telegram guard:** при `rules_not_ready` / `rules_invalid` — **deny** команд (`access_guard`, fail-closed).

**Alternatives**  
Fail-open для команд — не выбрано (guard возвращает deny).

**Consequences**  
Анализаторы могут работать на stale rules; бот блокируется при битых rules.

**Risks**  
Расхождение: `ARCHITECTURE.md` описывает reload «перед каждой командой» — фактически TTL + invalidate на `/reload_rules`.

**Evidence**  
`rules_provider.py` except branch, `access_guard.py`, `ARCHITECTURE.md` §3.3.

---

### E9 — Restart через `os._exit(1)` и grace для active jobs

| Поле | Значение |
|------|----------|
| **Decision ID** | E9 |
| **Status** | CONFIRMED |

**Context**  
Long-running процесс с Playwright и утечками памяти/сессий нуждается в периодическом hard refresh.

**Decision**  
Поток `restart_worker`: фиксированные моменты MSK `(10:30, 13:30, 16:30, 19:30, 22:30, 01:30)` → ждать `active_jobs()==0` до `RESTART_GRACE_MIN` (default 10 min) → **`os._exit(1)`** для рестарта контейнера Railway.

**Alternatives**  
In-process reload, supervisor SIGHUP, rolling deploy only — не в коде.

**Consequences**  
Жёсткий обрыв процесса; polling и threads прекращаются вместе.

**Risks**  
`run_daily_conversion_loop` **не** в счётчике `_active_jobs` — может прерваться в окне 00:00 (I5 / U8).

**Evidence**  
`scheduler.py` `restart_worker`, `RESTART_TIMES`, `job_start`/`job_end`.

---

### E10 — File lock для Dropbox analyze pipeline

| Поле | Значение |
|------|----------|
| **Decision ID** | E10 |
| **Status** | CONFIRMED |

**Context**  
Параллельный запуск `main.process_file` / downloader analyze могут конфликтовать.

**Decision**  
Файловая блокировка `/tmp/dropbox_pipeline.lock` с PID; stale timeout **600** сек (`run_once_guard`).

**Alternatives**  
Distributed lock (Redis) — нет; TG `_running_lock` только для manual jobs.

**Consequences**  
Второй процесс пропускает analyze (`acquire_lock` → False).

**Risks**  
Lock только на части путей (main CLI, downloader analyze); scheduler Raccoon jobs **не** используют этот lock.

**Evidence**  
`run_once_guard.py`, `main.py`, `integrations/downloader.py`.

---

### E11 — Hourly report deduplication по fingerprint hash

| Поле | Значение |
|------|----------|
| **Decision ID** | E11 |
| **Status** | CONFIRMED |

**Context**  
Raccoon hourly job каждые 5 минут не должен спамить одинаковым отчётом.

**Decision**  
`_calc_fingerprint` (rows, totals, max_dt по payin) → sha256 `hash` → сравнение с `/tmp/hourly_raccoon/last_sent.json`; при совпадении — skip send.

**Alternatives**  
Всегда слать; cron раз в час — отвергнуто в пользу content-based dedup.

**Consequences**  
Меньше шума в Telegram; при сбое state-файла возможен повтор или пропуск — edge cases.

**Risks**  
State file локальный ephemera контейнера — при рестарте без сохранения volume hash сбрасывается (**UNKNOWN** Railway volume).

**Evidence**  
`analyzers/raccoon_hourly_report.py`, `contracts.md` DTO-08, FC-005.

---

### E12 — Manual commands: single-flight async executor

| Поле | Значение |
|------|----------|
| **Decision ID** | E12 |
| **Status** | CONFIRMED |

**Context**  
Оператор запускает тяжёлые sync job (Playwright) из Telegram без блокировки polling.

**Decision**  
`_run_job`: `asyncio.Lock` — один manual job; выполнение в `run_in_executor`; статус `/status`; traceback в чат при ошибке.

**Alternatives**  
Очередь job, Celery — нет.

**Consequences**  
Второй `/run_*` получает отказ «Уже выполняется»; scheduled threads могут идти параллельно manual job.

**Risks**  
Нет глобальной координации manual vs scheduled Playwright (разные auth state files).

**Evidence**  
`integrations/tg_commands.py`, `contracts.md` RT-011, SCH-01.

---

### E13 — Timezone Europe/Moscow (MSK) для расписаний и отчётов

| Поле | Значение |
|------|----------|
| **Decision ID** | E13 |
| **Status** | CONFIRMED |

**Context**  
Операции и отчёты привязаны к московскому операционному дню.

**Decision**  
`ZoneInfo("Europe/Moscow")` в `scheduler.py`, raccoon modules, hourly report; `downloader.py` additionally sets `os.environ["TZ"]="Europe/Moscow"`.

**Alternatives**  
UTC-only — не выбрано для scheduler/reports.

**Consequences**  
Все cron-like окна в MSK; Playwright contexts часто `timezone_id="Europe/Moscow"`.

**Risks**  
Смешение TZ env (только в downloader) vs explicit ZoneInfo elsewhere.

**Evidence**  
`scheduler.py` MSK, `raccoon_hourly_report.py`, `raccoon_wallet_downloader.py`, `bakai_monitor` window 08:00–23:55 MSK.

---

### E14 — Playwright + headless Chromium для web export

| Поле | Значение |
|------|----------|
| **Decision ID** | E14 |
| **Status** | CONFIRMED |

**Context**  
Кабинеты Antares/Raccoon/Bakai не дают стабильного API для выгрузки Excel.

**Decision**  
**Playwright** sync API, Chromium headless (`PLAYWRIGHT_HEADLESS`), `storage_state` JSON для сессий; install в Nixpacks build.

**Alternatives**  
Official API, manual upload only — частично остаётся через Dropbox manual drop.

**Consequences**  
Хрупкость к UI changes; тяжёлый контейнер.

**Risks**  
Selector breakage; long download timeouts (90s–360s).

**Evidence**  
`integrations/*_downloader*.py`, `bakai_monitor_playwright.py`, `nixpacks.toml`.

---

### E15 — Разделение YAML (архитектура) и rules.xlsx (бизнес)

| Поле | Значение |
|------|----------|
| **Decision ID** | E15 |
| **Status** | CONFIRMED |

**Context**  
Нужны стабильные параметры партнёров/колонок в git и гибкие бизнес-пороги вне deploy.

**Decision**  
- **Repo YAML:** `config/conversion_config.yaml`, `payout_config.yaml`, `wallet_config.yaml`, `raccoon_*`, `analysis_map.yaml`.  
- **rules.xlsx:** лимиты, exclude_time, thresholds_partner, ACL.  
- **Приоритет** (документ): `rules.xlsx > *.yaml > код` (`PROJECT_REFERENCE`).

**Alternatives**  
Всё в коде или всё в Excel — отвергнуто гибридом.

**Consequences**  
`conversion` использует YAML pools/exclude; wallet/raccoon wallet — rules sheets (**два механизма exclude**).

**Risks**  
Дублирование политик exclude (YAML vs rules) — операционная путаница.

**Evidence**  
`analyzers/conversion.py`, `wallet_analyzer.py`, `PROJECT_REFERENCE` §2, `CONTRACT_RULES.md`.

---

### E16 — Scheduled background errors: log and continue

| Поле | Значение |
|------|----------|
| **Decision ID** | E16 |
| **Status** | CONFIRMED |

**Context**  
Один упавший цикл не должен валить весь процесс (бот + другие циклы).

**Decision**  
`run_every_minutes` / `run_hourly_at_minute`: `try/except` → `log.exception` → `finally: job_end()` → sleep → next iteration.

**Alternatives**  
Crash process on job failure — не для scheduled loops (restart worker отдельно по расписанию).

**Consequences**  
Устойчивость; тихие повторяющиеся ошибки возможны без alert escalation.

**Evidence**  
`scheduler.py`, `contracts.md` SCH-02/03, `ARCHITECTURE.md` job policy (документ).

---

### E17 — Analyzer routing по имени файла (`analysis_map.yaml`)

| Поле | Значение |
|------|----------|
| **Decision ID** | E17 |
| **Status** | CONFIRMED |

**Context**  
Разные типы Excel (conversion, payout) требуют разных analyzers.

**Decision**  
`get_analyzer(filename)` — substring match `file_pattern` из `config/analysis_map.yaml`; card/cd обязателен для conversion/payout.

**Alternatives**  
MIME/type detection, отдельные очереди — нет.

**Evidence**  
`analyzers/selector.py`, `main.py`, `architecture_map.md` P1.

---

## Dormant Decisions

### D1 — Antares hourly как scheduled pipeline

| Поле | Значение |
|------|----------|
| **Decision ID** | D1 |
| **Status** | DORMANT |

**Context**  
Предполагались hourly отчёты Antares (`hourly_downloader` + `analyzers/hourly_report.py`, `TELEGRAM_CHAT_ID_HOURLY`).

**Decision (intended)**  
Периодическая выгрузка payin/payout за сегодня и отчёт в Telegram.

**Evidence от dormant**  
Модули существуют; **нет импорта** из `scheduler.py` / `tg_commands.py`.

**Evidence**  
`integrations/hourly_downloader.py`, `analyzers/hourly_report.py`, `architecture_map.md` R8.

---

### D2 — Observability Plane: append-only events

| Поле | Значение |
|------|----------|
| **Decision ID** | D2 |
| **Status** | DORMANT |

**Context**  
`ARCHITECTURE.md` описывает события, `rules_version` в job, cold storage `events_*.jsonl`.

**Decision (intended)**  
Структурированный audit trail.

**Evidence от dormant**  
`core/events.py` — **пустой файл**; нет записи событий в scanned runtime.

**Evidence**  
`ARCHITECTURE.md` §2, §6; `CONTRACT_BOT.md` event types — DOCS_ONLY для edit flows.

---

### D3 — Alternate rules delivery (`download_rules_xlsx`)

| Поле | Значение |
|------|----------|
| **Decision ID** | D3 |
| **Status** | DORMANT |

**Decision**  
Канонический путь `DROPBOX_RULES_PATH` → `RULES_LOCAL_PATH` (`/tmp/rules/rules.xlsx`).

**Evidence от dormant**  
Функция в `dropbox_watcher.py`; **0 callers**; активен `rules_provider` → `/tmp/rules_cache/`.

**Evidence**  
`dropbox_watcher.py`, `contracts.md` RT-D01.

---

### D4 — Отдельные `roles.yaml` / `policies.yaml` в repo

| Поле | Значение |
|------|----------|
| **Decision ID** | D4 |
| **Status** | DORMANT (as documented layout) |

**Decision (in ARCHITECTURE.md)**  
Roles/policies в `/config/access/*.yaml`.

**Evidence от dormant**  
ACL реализован листами `access`/`commands` в **rules.xlsx**; файлов `config/access/` в repo — **не найдено** в architecture scan.

**Evidence**  
`ARCHITECTURE.md` §3.1 table vs `core/access_rules.py`.

---

## Questionable Decisions

### Q1 — Два environment variable для одного Telegram-бота

| Поле | Значение |
|------|----------|
| **Decision ID** | Q1 |
| **Status** | STALE_RISK (не подтверждено как намеренное решение) |

**Context**  
Polling и outbound используют разные имена env.

**Observed state**  
- `TG_BOT_TOKEN` — `scheduler.py` Application.  
- `TELEGRAM_BOT_TOKEN` — `telegram_bot.py` (import-time required).

**Was it a decision?**  
**UNKNOWN** — может быть историческая миграция или oversight.

**Evidence**  
`scheduler.py`, `telegram_bot.py`, `current_state.md` U1, `contracts.md` X-01.

---

### Q2 — Имя `scheduler.py` vs фактическая роль

| Поле | Значение |
|------|----------|
| **Decision ID** | Q2 |
| **Status** | STALE_RISK |

**Observed**  
Код: bot runner + Raccoon scheduler + restart.  
`PROJECT_REFERENCE`: «не планировщик», «тонкий entrypoint», «не запускает job автоматически» — **опровергнуто кодом**.

**Evidence**  
`PROJECT_REFERENCE.md` § scheduler vs `scheduler.py` threads.

---

### Q3 — In-process `last_card_path` для pairing card/conversion

| Поле | Значение |
|------|----------|
| **Decision ID** | Q3 |
| **Status** | CONFIRMED (код); **QUESTIONABLE** (design quality) |

**Decision**  
Глобальная переменная в `main.py` для card-файла между вызовами в **одном процессе**.

**Risks**  
Не работает между отдельными CLI invocations; race при параллели — mitigated частично lock.

**Evidence**  
`main.py` `last_card_path`, `architecture_map.md` INV10.

---

### Q4 — Antares full downloader вне scheduler

| Поле | Значение |
|------|----------|
| **Decision ID** | Q4 |
| **Status** | UNKNOWN (намеренно отдельный сервис или забытый cron) |

**Observed**  
`run_download()` полный pipeline Antares → Dropbox → `process_file`; не в `railway.toml`.

**Evidence**  
`integrations/downloader.py`, `current_state.md` DOR-01, U2.

---

## Documentation-only Decisions

### DOC1 — Правила редактируются только через Telegram-бот

| Поле | Значение |
|------|----------|
| **Decision ID** | DOC1 |
| **Status** | DOCS_ONLY |

**Stated decision**  
`CONTRACT_BOT.md`, `RULES_EDITING.md`: бот — единственная рекомендованная точка изменения; атомарность; audit events; no manual Excel.

**Runtime**  
Handlers: `/reload_rules` only; **нет** write/upload rule commands в `tg_commands.py`.

**Evidence**  
`CONTRACT_BOT.md`, `RULES_EDITING.md`, `tg_commands.py`, `current_state.md` U4.

---

### DOC2 — Отказ job при несовпадении `meta.version` rules

| Поле | Значение |
|------|----------|
| **Decision ID** | DOC2 |
| **Status** | DOCS_ONLY |

**Stated**  
`CONTRACT_RULES.md`: version mismatch → отказ запуска job.

**Runtime**  
**UNKNOWN** — чтение листа `meta` в Python не подтверждено в Stage A1–A4 scan.

**Evidence**  
`CONTRACT_RULES.md` §3.1; grep meta sheet readers — absent.

---

### DOC3 — Execution Plane не интерпретирует правила

| Поле | Значение |
|------|----------|
| **Decision ID** | DOC3 |
| **Status** | DOCS_ONLY (идеал) / **STALE_RISK** (частично нарушено) |

**Stated**  
`ARCHITECTURE.md` §2.1: Execution не содержит бизнес-решений.

**Runtime**  
Wallet/raccoon analyzers **читают и применяют** `rules.xlsx` (`exclude_time`, limits, thresholds) — исполнение с интерпретацией правил в analyzer layer.

**Evidence**  
`ARCHITECTURE.md`, `wallet_analyzer.py`, `raccoon_wallet_analyzer.py`.

---

### DOC4 — `PROJECT_REFERENCE`: scheduler без auto-jobs

| Поле | Значение |
|------|----------|
| **Decision ID** | DOC4 |
| **Status** | DOCS_ONLY / STALE_RISK |

**Stated**  
Scheduler не планировщик, не job-движок.

**Runtime**  
CONFIRMED противоречие — см. E6, Q2.

---

## Implicit invariants (I#)

| ID | Инвариант | Нарушение = | Статус |
|----|-----------|-------------|--------|
| I1 | Dropbox pipeline lock stale **600s** | Пропуск или параллельный analyze | CONFIRMED |
| I2 | `send_message_sync` / `send_file_sync` без `chat_id` → `ValueError` | Runtime error | CONFIRMED |
| I3 | Prod entry без `TG_BOT_TOKEN` → no start | Container exit on boot | CONFIRMED |
| I4 | Import `telegram_bot` без `TELEGRAM_BOT_TOKEN` → `ValueError` | Import chain fail | CONFIRMED |
| I5 | Daily conversion loop вне `_active_jobs` | Restart может прервать 00:00 job | STALE_RISK |
| I6 | DORMANT modules не в scheduler import graph | Не стартуют в prod | CONFIRMED |
| I7 | DOCS_ONLY bot rule edit не активируется без новых handlers | Ops manual Dropbox edit | CONFIRMED |

---

## Summary index (E# quick reference)

| ID | Краткое решение | Статус |
|----|-----------------|--------|
| E1 | Railway + Nixpacks | CONFIRMED |
| E2 | `scheduler.py` prod entry | CONFIRMED |
| E3 | Dropbox transport/SoT files | CONFIRMED |
| E4 | `rules.xlsx` Control Plane | CONFIRMED |
| E5 | Telegram ops UI + alerts | CONFIRMED |
| E6 | Raccoon scheduled in scheduler | CONFIRMED |
| E7 | Cache-before-runtime | CONFIRMED |
| E8 | Fail-safe rules / fail-closed ACL | CONFIRMED |
| E9 | `os._exit(1)` restart | CONFIRMED |
| E10 | `/tmp/dropbox_pipeline.lock` | CONFIRMED |
| E11 | Hourly fingerprint dedup | CONFIRMED |
| E12 | Manual TG single-flight jobs | CONFIRMED |
| E13 | MSK timezone | CONFIRMED |
| E14 | Playwright exports | CONFIRMED |
| E15 | YAML + rules.xlsx split | CONFIRMED |
| E16 | Scheduled errors log+continue | CONFIRMED |
| E17 | `analysis_map.yaml` routing | CONFIRMED |

---

## DORMANT activation log

| Module | Decision | Date | Task |
|--------|----------|------|------|
| `hourly_downloader` + `hourly_report` | D1 pending | — | Confirm retire or wire to scheduler |
| `core/events.py` | D2 pending | — | Implement or remove from ARCHITECTURE |
| `download_rules_xlsx` | D3 pending | — | Deprecate or merge with rules_provider |
| Bot rule edit handlers | DOC1 pending | — | TASK-A3-04 area |

---

## Отменённые / superseded

| ID | Было | Заменено на | Дата |
|----|------|-------------|------|
| — | *(не выявлено в источниках)* | — | — |

**UNKNOWN:** были ли явные отмены Antares hourly в commit history — не проверялось в Stage A4.

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Stage A4: журнал создан из KB + codebase + CONTRACT_*/ARCHITECTURE/PROJECT_REFERENCE |
