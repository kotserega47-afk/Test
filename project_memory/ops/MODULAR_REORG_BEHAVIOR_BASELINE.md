# Эталон текущего поведения (TASK-2026-09-17-04)

| Мета | Значение |
|------|----------|
| **Статус** | DRAFT characterization; **не** production |
| **Test worktree SHA** | `455eb180df4d31e353595b314377abce5801c4ad` (ветка `feat/task-2026-09-17-04-behavior-baseline`) |
| **Не выпущенный parser** | PR #5 `acfb9958df644679b85feecaf4e6a9acf65b884b` |
| **Не выпущенный gate** | PR #6 code `48a2a825ce3d04d27501d1a387bb5c4b8b5dd9c9` (docs `ae0aeda…`) |
| **Prod Test (survey F25)** | `test_main` `535994c…` — **не** эта ветка |
| **Prod Raccoon (survey F26)** | Platform `develop` `ebbcd6c11b0c2418575f807edd40feb1c4fed468` (клон `Platform_2.0_survey`) |

Поведение Draft PR **не** называть действующим в production.  
`JOB_REGISTRY` / список команд — **регистрация в коде**, не доказательство, что job включён в `rules.xlsx` schedules.

---

## 1. Источники эталона

Правила для строк ниже: synthetic snapshot **в тесте**, не prod `rules.xlsx`. Prod-расписание без выгрузки workbook **не** воспроизводится.

### Test — отчёты

| ID | Репо / SHA | Функция | Вход | Правила | Ожидаемый результат | Воспроизведение |
|----|------------|---------|------|---------|---------------------|-----------------|
| T-H1 | Test, этот PR SHA | `reporters.hourly_reporter.render_hourly` | DTO в `tests/test_hourly_render_golden.py` | synthetic `RulesSnapshotV2` layout items в тесте | `tests/fixtures/hourly/golden/expected_hourly_render.txt` | `pytest tests/test_hourly_render_golden.py` |
| T-H2 | Test, этот PR SHA | `analyzers.hourly_analyzer.build_hourly_dto_from_files` → `render_hourly` | synthetic payin/payout xlsx в тесте (партнёры Hourly Alpha/Beta, даты 15.01.2026) | synthetic snapshot в тесте (`ruleset_version=golden-hourly-pipeline`) | `tests/fixtures/hourly/golden/expected_hourly_pipeline.txt` | `pytest tests/test_hourly_pipeline_golden.py` |
| T-W1 | Test, этот PR SHA | `reporters.wallet_reporter.render_wallet` | `WalletStatsDTO` + layout DataFrame в тесте | layout-колонки в тесте, не workbook | `tests/fixtures/wallet/golden/expected_wallet_main.txt`, `expected_wallet_alerts.txt` | `pytest tests/test_wallet_render_golden.py` |
| T-C1 | Test, этот PR SHA | `analyzers.conversion` + `reporters.conversion_reporter.render_excel` / `render_telegram` | `tests/fixtures/conversion/fixture_data.py` (CARD001/002, партнёры Test Partner/Test Beta) | `_build_snapshot()` в `test_conversion_characterization.py` | `tests/fixtures/conversion/golden/expected_*.json` (контракт листов + фрагменты TG + summary) | `pytest tests/test_conversion_characterization.py` |
| T-C2 | Test, этот PR SHA | `integrations.conversion_pipeline` fingerprint | synthetic files в тесте | n/a | **текущее:** совпадение fingerprint **не** пропускает `conversion.run` | `pytest tests/test_conversion_fingerprint.py::TestConversionFingerprintPipeline::test_passive_fingerprint_does_not_skip_conversion_run` |
| T-R1 | Test, этот PR SHA | `aggregate_payin_by_method` + `format_report` | три строки Alpha/Beta SBP+Card, 20.05.2026 14:30 | нет YAML в этом пути | `tests/fixtures/raccoon/golden/expected_payin_by_method.txt` (написан вручную) | `pytest tests/test_raccoon_payin_format_golden.py::test_raccoon_payin_format_matches_handwritten_golden` |
| T-R2 | Test, этот PR SHA | `_calc_fingerprint` | те же классы, что `tests/test_raccoon_hourly_by_method.py` | n/a | одинаковые строки → одинаковый hash; новая строка → другой hash | `pytest tests/test_raccoon_hourly_by_method.py` |
| T-I1 | Test, этот PR SHA | регистрация в исходниках | `tg_commands.py`, `raccoon_jobs.py`, `script_jobs/registry.py` | n/a | `tests/fixtures/behavior_baseline/expected_*.json` | `pytest tests/test_behavior_baseline_inventory.py` |

### Test — не эталон расчёта отчёта

| Тема | Покрытие | Комментарий |
|------|----------|-------------|
| Wallet **аналитика** с xlsx | нет эталона | есть только render DTO; `build_wallet_stats_dto` ходит в `get_snapshot_v2` |
| Payout как отдельный Excel-отчёт | частично | суммы payout в T-H2; конфиг `tests/test_payout_config_migration.py` (schema/YAML≠rules), не полный отчёт |
| Raccoon `raccoon_wallet` job | wiring | downloader cycle, не текстовый golden |
| Raccoon daily conversion текст | частично | `tests/test_raccoon_hourly_conversion.py` факты/пороги, нет handwritten full message |
| Prod schedules | **нет** | `load_schedules()` читает **текущий** rules workbook; без обезличенного prod dump нельзя сказать, какие job **включены** |

### Platform_2.0 (отдельный каталог, SHA `ebbcd6c…`)

| ID | Функция | Вход | Ожидание | Воспроизведение |
|----|---------|------|----------|-----------------|
| P-R1 | тот же `format_report` в клоне Platform | те же три строки, что T-R1 | тот же `expected_payin_by_method.txt` | `pytest tests/test_raccoon_payin_format_golden.py::test_raccoon_payin_format_same_on_platform_survey_clone` (cwd/PYTHONPATH = `Platform_2.0_survey`; модули **не** копируются в Test) |

Import-time на Platform: нужны `TELEGRAM_CHAT_ID_HOURLY_RACCOON` и `TELEGRAM_CHAT_ID_RACCOON_WALLET` (иначе `RuntimeError`). Это отличие от Test (lazy chat id).

### Parser / gate (ещё не в production)

| ID | SHA | Что это | Production? |
|----|-----|---------|-------------|
| G-02 | `acfb9958…` | чистый `parse_project_profile` | нет |
| G-03 | `48a2a82…` | `enforce_legacy_scheduler_profile` до mixed imports | нет |

Происхождение проверки парсера: GPT отдельно прогнал **18** тестов на `acfb995…` в изолированной директории; полный набор проекта не запускался. **41 passed** на более позднем HEAD — прогон Cursor (файлы parser+gate).

---

## 2. Карта покрытия

| Семейство | Статус | Конкретные тесты | Пробел |
|-----------|--------|------------------|--------|
| Antares hourly render | покрыт | `test_hourly_render_golden.py` | не считает DTO из файлов |
| Antares hourly pipeline | покрыт | `test_hourly_pipeline_golden.py` | узкий набор партнёров/статусов |
| Hourly layout/hide inactive | частично | `test_hourly_reporter_layout.py`, `test_hourly_hide_inactive_rows.py`, `test_hourly_payout_terminal_render.py`, `test_hourly_payin_partner_resolution.py`, `test_hourly_scheduler_gate.py` | не полный golden-текст |
| Antares wallet render | покрыт | `test_wallet_render_golden.py` | нет file→DTO |
| Antares conversion calc+TG+Excel contract | частично | `test_conversion_characterization.py` | Excel: имена листов/колонки/карточка, **не** все ячейки и оформление |
| Conversion skip unchanged | покрыт **как «не skip»** | `test_conversion_fingerprint.py` (`test_passive_fingerprint_does_not_skip_conversion_run`); observation `test_conversion_fp_observation.py` | CONV-OPTIMIZATION-PHASE-1B не включён: эталон — **повторный run**, не пропуск |
| Payout | частично | pipeline T-H2 + `test_payout_config_migration.py` | нет отдельного payout-report golden |
| Raccoon payin format | покрыт (новый T-R1) | `test_raccoon_payin_format_golden.py`; агрегаты `test_raccoon_hourly_by_method.py` | полный prod YAML layout не используется |
| Raccoon conversion alerts | частично | `test_raccoon_hourly_conversion.py` | нет полного текста алерта как файла |
| Raccoon jobs/commands wiring | покрыт | `test_raccoon_job_registry.py`, `test_raccoon_tg_commands.py` | не расписание |
| Commands + JOB_REGISTRY freeze | покрыт (регистрация) | `test_behavior_baseline_inventory.py` | не live rules |
| WE terminal / unread chips / add-block Save | покрыт (не browser) | `tests/unit/test_wallet_editor_terminal_field.py`, `test_add_wallet_engine.py`, `test_add_wallet_contract.py` | Playwright live DOM нет |
| WE HOLD | покрыт unit | `test_wallet_editor_hold_enforcement.py` | — |
| PID locks | покрыт | `tests/test_job_runner_stale_lock.py`, `tests/test_lock_status.py` | не межконтейнерная координация |

Не дублировать T-H1/T-W1/T-C1 новыми теми же сценариями.

---

## 3. Команды и источники расписаний (Test code)

**Триггер «schedule»** = строка в rules `schedules` → `core.schedules.load_schedules` → `scheduler.schedule_loop`. Какие `job_key` **enabled в prod** — UNKNOWN без workbook (U3).

| job_type | Регистрация | Типичный код-триггер | TG команда |
|----------|-------------|----------------------|------------|
| `hourly` | `tg_commands.JOB_REGISTRY` | rules schedule + hourly gate | `/run_hourly` |
| `wallet` | то же | rules schedule | `/run_wallet` |
| `download` | то же | rules schedule | `/run_download` |
| `rate` | то же | rules schedule | `/run_rate` |
| `wallet_editor_registry_refresh` | то же | schedule если есть в rules | `/wallet_editor_refresh` |
| `wallet_editor_registry_replay` | то же | schedule / `/registry_replay` | `/registry_replay` |
| `raccoon_wallet` | `raccoon_jobs` | **если** rules; факт prod Test = U12 | `/run_raccoon` |
| `raccoon_hourly` | то же | **если** rules; U12 | `/run_hourly_raccoon` |
| `raccoon_daily_conversion` | то же | **если** rules; U12 | нет отдельной команды |
| `script_job:hello_world` | script_jobs | `/run_script_hello` | `/run_script_hello` |
| `script_job:operator_wallets_ready` | то же | `/operator_wallets_ready` | `/operator_wallets_ready` |

Прочие команды без job_type: `/start` `/help` `/status` `/whoami` `/reload_rules` `/rules_validate` `/auto_enable_*` `/registry_health` `/registry_export` + document handler.

---

## 4. Raccoon Test vs Platform `ebbcd6c…`

Прогон одинаковых синтетических payin-строк: см. T-R1 / P-R1. **Не копировать** модули Platform в Test.

### Совпадает (на этом входе)

Агрегация method→partner и текст `format_report` (тысячные пробелы, заголовок `00:00–14:30`, без ₽).

### Различается (код, не чинилось)

| Тема | Test | Platform `develop` |
|------|------|--------------------|
| Token | `TELEGRAM_BOT_TOKEN` | `TG_BOT_TOKEN` |
| Payin chat | lazy `_hourly_chat_id()` / `TELEGRAM_CHAT_ID_HOURLY_RACCOON` | **import-time raise**, если нет `TELEGRAM_CHAT_ID_HOURLY_RACCOON` |
| Wallet chat env | lazy | import-time raise `TELEGRAM_CHAT_ID_RACCOON_WALLET` |
| Расписание | Rules V2 `load_schedules` | hardcoded hourly + `RACCOON_REPORTER_EVERY_MIN` (default 10) + окно daily conversion |
| Restart | нет в Test scheduler | `RESTART_TIMES` |
| Locks | PID files `{STATE_DIR}/locks/{job_type}.lock` | in-memory / asyncio job lock |
| Команды | mixed Antares+WE+Raccoon | + `/run_raccoon_analyzer` `/run_raccoon_reporter`; `/run_wallet` stub; нет WE ingest |
| Job names | `raccoon_hourly` = payin report + downloader chain | тот же файл `raccoon_hourly_report.py` в логах часто `wallet_report` |
| Raccoon wallet analyzer | Test job = Playwright cycle | `analyzers/raccoon_wallet_analyzer.py` + YAML `config/raccoon_wallet_config.yaml` |
| Config | Rules V2 | YAML `config/raccoon_hourly_report.yaml` / `raccoon_wallet_config.yaml` |

### Подозрение (не «исправить молча»)

- **Имена jobs vs логи:** Test `raccoon_hourly` пишет `[raccoon_hourly_report]`; Platform `format_report` путь логирует `[wallet_report]`. Путаница WalletReporter vs hourly при переносе.
- **Два chat env** на одном модуле (payin vs conversion alerts) легко перепутать при cutover.
- **U12:** наличие jobs в Test не значит, что они исполняются на сервисе Test.

`raccoon_wallet_analyzer` Platform **не** прогонялся тем же xlsx в этой задаче: другой контракт колонок/YAML, нет общего обезличенного входа без копирования конфига.

---

## 5. Что эталоны позволят проверить при минимальном переносе

Можно сверять после move **тех же функций**:

- hourly render + file pipeline;
- wallet **render**;
- conversion characterization (TG fragments + excel sheet contract);
- raccoon payin `format_report`;
- список JOB_REGISTRY keys и CommandHandler names;
- WE terminal/add-block/HOLD unit;
- PID stale lock.

**Нельзя** объявлять готовность полного переноса: нет file→wallet DTO golden; нет полного conversion Excel cell-level; нет prod schedules dump; нет raccoon daily conversion full text; нет Platform wallet-analyzer golden; skip unchanged для Antares conversion **ещё не поведение prod** (сейчас fingerprint пассивный).

---

## 6. Чеклист регрессии (зелёный после move тех функций)

```
pytest tests/test_hourly_render_golden.py tests/test_hourly_pipeline_golden.py
pytest tests/test_wallet_render_golden.py
pytest tests/test_conversion_characterization.py tests/test_conversion_fingerprint.py
pytest tests/test_raccoon_payin_format_golden.py tests/test_raccoon_hourly_by_method.py
pytest tests/test_behavior_baseline_inventory.py tests/test_raccoon_job_registry.py
pytest tests/unit/test_wallet_editor_terminal_field.py tests/unit/test_add_wallet_engine.py
pytest tests/test_job_runner_stale_lock.py
```
