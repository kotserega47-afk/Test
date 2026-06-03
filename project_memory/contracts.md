# Contracts — analizis

| Мета | Значение |
|------|----------|
| **KB версия** | v1.3 |
| **Последнее обновление** | 2026-06-02 |

---

## Status

| Поле | Значение |
|------|----------|
| **Документ** | active — G5 Data Contracts заполнен (material) |
| **Источник** | runtime `.py` + `config/*` + `core/rules_v2/contract_schema.py` |
| **Секреты** | значения env **не** хранятся в KB |

Легенда критичности: **CRITICAL** | **IMPORTANT** | **OPTIONAL**

Легенда статуса: **CONFIRMED** | **UNKNOWN**

---

## Purpose

Единый реестр контрактов данных: env, файлы, Excel/CSV/YAML/JSON, data flows, locks, fingerprints.

**G5** закрыт TASK-2026-05-31-03. Остаточные UNKNOWN — в § Remaining UNKNOWN.

---

## 1. Environment variables (полный inventory)

| Variable | Обязательна | Default | Где используется | Влияние на runtime | Критичность | Статус |
|----------|-------------|---------|------------------|-------------------|-------------|--------|
| `TELEGRAM_BOT_TOKEN` | да (prod start) | — | `scheduler.py` L33, `telegram_bot.py` L15 | без token процесс не стартует | CRITICAL | CONFIRMED |
| `RULES_XLSX_PATH` | да | — | `tg_commands.py`, `rules_provider.py`, `state_provider.py`, `state_store.py`, `identity_registry_io.py` | access, schedules, job_params, state paths | CRITICAL | CONFIRMED |
| `STATE_DIR` | нет | `/data/state` | `job_runner.py`, `event_log.py`, `lock_status.py`, `observability/conversion_fp_observation.py` | locks, event log, observation JSONL | CRITICAL | CONFIRMED |
| `ANTARES_LOGIN` | да (download jobs) | — | `downloader.py`, `hourly_downloader.py`, `downloader_wallets.py` | job fail at runtime | IMPORTANT | CONFIRMED |
| `ANTARES_PASSWORD` | да (download jobs) | — | same | job fail at runtime | IMPORTANT | CONFIRMED |
| `PLAYWRIGHT_HEADLESS` | нет | `1` (truthy) | download modules | browser launch mode | OPTIONAL | CONFIRMED |
| `TELEGRAM_CHAT_ID_HOURLY` | да (send path) | — | `tg_commands.run_hourly_job` L116 | `RuntimeError` if send needed | IMPORTANT | CONFIRMED |
| `TELEGRAM_CHAT_ID_WALLET` | нет | `""` | `downloader_wallets.py` L242 | skip send if empty | IMPORTANT | CONFIRMED |
| `TELEGRAM_CHAT_ID_ANALIZ` | да (module import) | — | `main.py`, `downloader.py`, `payout.py`, `conversion.py` | import-time or notify fail | IMPORTANT | CONFIRMED |
| `CURRENT_RATE_BAKAI_CHAT_ID` | да (module import) | — | `bakai_monitor_playwright.py` L17 | `RuntimeError` at import | IMPORTANT | CONFIRMED |
| `NEW_RATE_BAKAI_CHAT_ID` | да (module import) | — | `bakai_monitor_playwright.py` L22 | `RuntimeError` at import | IMPORTANT | CONFIRMED |
| `DROPBOX_INPUT_PATH` | да (P3/P5) | — | `main.py`, `downloader.py`, `payout.py` | analyze/download fail | IMPORTANT | CONFIRMED |
| `DROPBOX_PROCESSED_PATH` | да (P3/P5 moves) | — | `main.py`, `payout.py` | move after analyze fail | IMPORTANT | CONFIRMED |
| `DROPBOX_ACCESS_TOKEN` | да* | — | `dropbox_watcher._get_dbx()` | Dropbox IO fail | IMPORTANT | CONFIRMED |
| `DROPBOX_REFRESH_TOKEN` | да* | — | same (alt auth) | Dropbox IO fail | IMPORTANT | CONFIRMED |
| `DROPBOX_APP_KEY` | да* | — | same (with refresh trio) | Dropbox IO fail | IMPORTANT | CONFIRMED |
| `DROPBOX_APP_SECRET` | да* | — | same | Dropbox IO fail | IMPORTANT | CONFIRMED |
| `DROPBOX_SPECIAL_PATH` | нет | `/Ostin/platform/special` | `conversion.py` L210 | special_cards optional | OPTIONAL | CONFIRMED |
| `DROPBOX_RULES_PATH` | нет | `/rules.xlsx` | `dropbox_watcher.py` L104 | legacy rules download path | UNKNOWN | CONFIRMED |
| `RULES_LOCAL_PATH` | нет | `/tmp/rules/rules.xlsx` | `dropbox_watcher.py` L105 | legacy local rules path | UNKNOWN | CONFIRMED |
| `RULES_SYNC_MIN_INTERVAL_SEC` | нет | `30` | `rules_provider.py` L241 | sync throttle | OPTIONAL | CONFIRMED |
| `STATE_SYNC_MIN_INTERVAL_SEC` | нет | `30` | `state_provider.py` L85 | state cache TTL | OPTIONAL | CONFIRMED |
| `RULES_CONTRACT_STRICT` | нет | off | `contract_publish.py` L56 | strict publish policy | OPTIONAL | CONFIRMED |
| `RULES_CONTRACT_SHADOW` | нет | off | `contract_publish.py` L58 | shadow validation | OPTIONAL | CONFIRMED |
| `PAYOUT_CONFIG_FROM_RULES_V2` | нет | `0` | `analyzers/payout_config_loader.py`, `analyzers/payout.py` | `0`: YAML primary + rules shadow compare; `1`: Rules V2 primary, YAML fallback | OPTIONAL | CONFIRMED |
| `RULES_IDENTITY_SAVE` | нет | off | `rules_provider.py` L166 | save registry after publish | OPTIONAL | CONFIRMED |
| `RULES_IDENTITY_COMPARE` | нет | — | `contract_publish.py` L140 | identity drift mode | OPTIONAL | CONFIRMED |
| `RULES_IDENTITY_DISABLED` | нет | off | `contract_publish.py` L138 | disable identity compare | OPTIONAL | CONFIRMED |
| `RULES_IDENTITY_REGISTRY_PATH` | нет | derived from `RULES_XLSX_PATH` | `identity_registry_io.py` L59 | override registry path | OPTIONAL | CONFIRMED |
| `RULES_VALIDATE_AUDIT_JSONL` | нет | — | `rules_validate_audit.py` L33 | audit log path | OPTIONAL | CONFIRMED |
| `RUNTIME_INSTANCE_ID` | нет | hostname | `rules_validate_audit.py` L42 | audit metadata | OPTIONAL | CONFIRMED |
| `OBSERVATION_ENABLED` | нет | off | `tg_commands.py` L47 | extended `/status` | OPTIONAL | CONFIRMED |
| `HOURLY_PAYIN_MAPPING_STRICT` | нет | off | `hourly_analyzer.py` L224 | strict partner mapping | OPTIONAL | CONFIRMED |
| `TMP` | нет | `/tmp` | `payout.py` L242 | temp report path | OPTIONAL | CONFIRMED |
| `WALLET_EDITOR_ALLOWED_CHAT_IDS` | да (ingest enabled) | `""` | `integrations/wallet_editor_tg.py` | fail-closed: пустой → все чаты отклонены | IMPORTANT | CONFIRMED |
| `WALLET_EDITOR_OPERATOR_MAP` | да (ingest enabled) | `""` | `automation/runtime.py` | fail-closed: unmapped user_id → отказ | CRITICAL | CONFIRMED |
| `WALLET_EDITOR_OPERATOR_<PROFILE>_LOGIN` | да (per mapped profile) | — | `automation/runtime.py` | incomplete profile → отказ | CRITICAL | CONFIRMED |
| `WALLET_EDITOR_OPERATOR_<PROFILE>_PASSWORD` | да (per mapped profile) | — | `automation/runtime.py` | incomplete profile → отказ | CRITICAL | CONFIRMED |
| `WALLET_EDITOR_AUTH_STATE_PATH` | нет | `/tmp/auth_state_wallet_editor.json` | `automation/runtime.py` `RunConfig` default | legacy default; production uses per-profile path | OPTIONAL | CONFIRMED |
| `WALLET_EDITOR_ANTARES_LOGIN` | нет | `""` | `automation/runtime.py` `RunConfig` default only | **не** используется production handler (WE-5) | OPTIONAL | CONFIRMED |
| `WALLET_EDITOR_ANTARES_PASSWORD` | нет | `""` | same | **не** используется production handler (WE-5) | OPTIONAL | CONFIRMED |
| `TELEGRAM_HEALTH_DEGRADED_THRESHOLD` | нет | `3` | `integrations/telegram_bot.py` | consecutive delivery failures → DEGRADED status/log | IMPORTANT | CONFIRMED |
| `TELEGRAM_HEALTH_LOG_INTERVAL_SECONDS` | нет | `600` | `integrations/telegram_bot.py`, `scheduler.py` | periodic `[TelegramSender/health]` log | OPTIONAL | CONFIRMED |
| `CONVERSION_WALLET_EDITOR` | нет (hook disabled if unset) | — | `integrations/conversion_wallet_editor_bridge.py` | Telegram chat for conversion→WE info/errors + WE result delivery | OPTIONAL | CONFIRMED |
| `CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_LOGIN` | да (hook enabled) | — | `integrations/conversion_wallet_editor_bridge.py` | scheduled conversion WE credentials (direct env, not operator map) | IMPORTANT | CONFIRMED |
| `CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_PASSWORD` | да (hook enabled) | — | same | scheduled conversion WE credentials | IMPORTANT | CONFIRMED |
| `DROPBOX_WALLET_EDITOR_PATH` | нет (registry skipped if unset) | — | `integrations/wallet_editor_registry.py` | cumulative WE results workbook in Dropbox | IMPORTANT | CONFIRMED |

### Conversion → Wallet Editor hook (scheduled)

| Variable | Format | Example |
|----------|--------|---------|
| `CONVERSION_WALLET_EDITOR` | Telegram chat id (int string) | `-1001234567890` |
| `CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_LOGIN` | Antares login for scheduled hook | `we-scheduled-login` |
| `CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_PASSWORD` | Antares password for scheduled hook | `(secret)` |

Rules:

- Hook is **best-effort** — missing any env → skip; conversion pipeline never fails
- Does **not** use `WALLET_EDITOR_OPERATOR_MAP` or Telegram user id routing
- Operator profile for queue/worker: `CONVERSION_AUTO` (system profile)
- Auth-state path: `/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json`
- Input Excel contract: columns `card`, `action`=`remove_partner`, `value`=`original_partner` from `problem_cards`
- All valid `problem_cards` rows forwarded (no per-run cap); invalid rows (empty `card` / `original_partner`) filtered; preserves order

### WalletEditor operator map format

| Variable | Format | Example |
|----------|--------|---------|
| `WALLET_EDITOR_OPERATOR_MAP` | comma-separated `telegram_user_id:PROFILE_KEY` pairs | `123456789:DENIS,987654321:IVAN` |

Rules:

- `PROFILE_KEY` normalized to uppercase; allowed chars: `[A-Z0-9_]`
- Credentials env per profile: `WALLET_EDITOR_OPERATOR_DENIS_LOGIN`, `WALLET_EDITOR_OPERATOR_DENIS_PASSWORD`
- Per-profile auth-state default: `/tmp/auth_state_wallet_editor_<PROFILE>.json`
- No fallback to `ANTARES_LOGIN` / `ANTARES_PASSWORD` / shared WalletEditor account in production handler

\* Dropbox auth: нужен **либо** `DROPBOX_ACCESS_TOKEN`, **либо** trio refresh (`DROPBOX_REFRESH_TOKEN` + `DROPBOX_APP_KEY` + `DROPBOX_APP_SECRET`) — `dropbox_watcher.py` L17–25.

---

## 2. File contracts (inventory)

| Файл / path pattern | Формат | Читает | Пишет | Обязательность | Если отсутствует | Критичность | Статус |
|---------------------|--------|--------|-------|----------------|------------------|-------------|--------|
| `rules.xlsx` (Dropbox → `/tmp/rules_cache/rules.xlsx`) | xlsx | `rules_provider`, `loader`, `access_rules`, `schedules`, `config_manager` | `rules_writer` (TG admin flows) | да (startup) | fail-fast at startup / sync error | CRITICAL | CONFIRMED |
| `config/payout_config.yaml` | yaml | `payout.py` via `payout_config_loader.py` (YAML default; Rules V2 optional) | — | да (payout job) | empty CONFIG; analysis degraded; Rules V2 fallback when flag=1 | IMPORTANT | CONFIRMED |
| `{RULES_FOLDER}/state/state.json` (Dropbox) | json | `state_store`, `state_provider` | `state_store`, `state_provider` | нет (bootstrap `{}`) | fp dedup reset; new state | IMPORTANT | CONFIRMED |
| `/tmp/state_store/state.json` | json | local cache `state_store` | `state_store` | transient | fallback local | IMPORTANT | CONFIRMED |
| `/tmp/state_cache/state.json` | json | `state_provider` cache | download cache | transient | re-download | OPTIONAL | CONFIRMED |
| `{STATE_DIR}/locks/{job_type}.lock` | text (PID) | `job_runner`, `lock_status` | `job_runner` | per job run | concurrent job rejected | IMPORTANT | CONFIRMED |
| `/tmp/dropbox_pipeline.lock` | text (PID) | `run_once_guard` | `run_once_guard` | P3 analyze phase | skip duplicate analyze | IMPORTANT | CONFIRMED |
| `/tmp/hourly/payin.xlsx`, `payout.xlsx` | xlsx | `hourly_report`, `hourly_analyzer` | `hourly_downloader` | P1 | skip / fail analyze | IMPORTANT | CONFIRMED |
| `/tmp/downloads/*`, `/tmp/wallet_handler/*` | xlsx | wallet/download jobs | Playwright downloaders | per job | job fail | IMPORTANT | CONFIRMED |
| `/tmp/auth_state.json` | json (Playwright storage) | `downloader.py` | Playwright context | нет | re-login | OPTIONAL | CONFIRMED |
| `/tmp/auth_state_wallets.json` | json (Playwright) | `downloader_wallets.py` | Playwright | нет | re-login | OPTIONAL | CONFIRMED |
| `/tmp/hourly/auth_state.json` | json (Playwright) | `hourly_downloader.py` | Playwright | нет | re-login | OPTIONAL | CONFIRMED |
| `/tmp/bakai_last_buy_rate.txt` | text (float) | `bakai_monitor_playwright` | same | нет | first-run baseline message | OPTIONAL | CONFIRMED |
| `{STATE_DIR}/events/events_YYYY-MM-DD.jsonl` | jsonl | ops/read | `event_log.append_event` | нет | observability gap | OPTIONAL | CONFIRMED |
| `{folder}/state/rules_identity_registry.v1.json` | json | `identity_registry_io`, `contract_publish` | `rules_provider` publish | нет | identity compare baseline missing | OPTIONAL | CONFIRMED |
| Dropbox `special_cards.xlsx` | xlsx | `conversion.py` | — | нет | analysis without special rules | OPTIONAL | CONFIRMED |
| Antares exports (`conversion_*`, `payout_*`, `card_*`, `cd_*`) | xlsx | analyzers via `main`/`download` | downloaders → Dropbox | P3 | pipeline skip/fail | IMPORTANT | CONFIRMED |
| Output reports (`report_*.xlsx`) | xlsx | — | `conversion.py`, `payout.py` | output | — | OPTIONAL | CONFIRMED |
| `/tmp/wallet_editor/wallet_editor_*.xlsx` | xlsx | WalletEditor worker | `wallet_editor_tg` download | P-WE input | re-download on retry | IMPORTANT | CONFIRMED |
| `/tmp/wallet_editor/wallet_editor_result_<INPUT>_<PROFILE>.xlsx` | xlsx | — | `automation/engine.py` via worker | P-WE output | collision → `_2` / `_<uuid8>` suffix | IMPORTANT | CONFIRMED |
| Dropbox `{DROPBOX_WALLET_EDITOR_PATH}` (e.g. `/Ostin/platform/Tests/wallet_editor.xlsx`) | xlsx | WalletEditor registry read/write | `integrations/wallet_editor_registry.py` | P-WE cumulative history | download/upload fail → log only; per-run TG unchanged | IMPORTANT | CONFIRMED |

Wallet Editor **result** xlsx columns (output): `Дата отключения` (MSK `ДД.ММ.ГГГГ ЧЧ:ММ:СС`, row processing time via `now_msk()`), `card`, `action`, `value`, `status`, `comment` — `automation/engine.py`.

Wallet Editor **Dropbox registry** xlsx (`integrations/wallet_editor_registry.py` + `wallet_editor_registry_lifecycle.py`):

| Sheet | Purpose |
|-------|---------|
| `all_results` | lifecycle table per result row (recalculated on every append) |
| `runs` | one row per WE run |
| `hold` | card+partner pairs excluded from re-enable |
| `Отлёжка` | partner → full days before re-enable |

**`all_results` columns (order):** `Дата отключения`, `Дата включения`, `Статус включения`, `Включено`, `Комментарий включения`, `card`, `partner`, `action`, `value`, `status`, `comment`, `hold`

**`runs` columns:** `started_at`, `finished_at`, `input_rows`, `success_rows`, `failed_rows`, `skipped_rows`, `output_file`

**Rules:** `partner` = `value` when `action=remove_partner`; `Дата включения` = disable date + `Полные дни` from `Отлёжка` (date only `dd.mm.yyyy`); missing partner on `Отлёжка` → `Нет даты отлёжки` + red fill + one-time TG warning per partner (`{STATE_DIR}/wallet_editor/missing_hold_days_warned.json`). Idempotency: `{STATE_DIR}/wallet_editor/registry_processed_run_ids.json`. Legacy `all_results` with `run_id` column auto-migrated.
| `/tmp/auth_state_wallet_editor_<PROFILE>.json` | json (Playwright) | `automation/engine.py` | Playwright per operator | нет | re-login for profile | IMPORTANT | CONFIRMED |
| `/tmp/auth_state_wallet_editor.json` | json (Playwright) | `RunConfig` default only | legacy default path | нет | legacy / tests | OPTIONAL | CONFIRMED |

---

## 3. Excel contracts

### 3.1 `rules.xlsx` (CONTRACT_V2)

Источник схемы колонок: `core/rules_v2/contract_schema.py` `SHEET_SCHEMAS`.

| Sheet | Required columns | Optional columns | Consumers | Критичность | Статус |
|-------|------------------|------------------|-----------|-------------|--------|
| `meta` | `key`, `value` | — | publish, versioning | CRITICAL | CONFIRMED |
| `exclude_time` | `id`, `enabled`, `analyzers`, `partner`, `start_dt`, `end_dt`, `reason` | `created_by`, `created_at` | `config_manager` | CRITICAL | CONFIRMED |
| `access` | `chat_id`, `user_id`, `level`, `enabled` | `note` | `access_rules` | CRITICAL | CONFIRMED |
| `commands` | `command`, `required_level`, `allow_private`, `allow_groups`, `enabled` | `note` | `access_rules`, TG guard | CRITICAL | CONFIRMED |
| `schedules` | `id`, `enabled`, `job_type`, `schedule_type`, `every_seconds`, `cron`, `jitter_sec`, `max_runtime_sec`, `coalesce` | deprecated: `Unnamed:*`, `cron.N` | `schedules.py`, scheduler | IMPORTANT | CONFIRMED |
| `job_params` | `id`, `enabled`, `job`, `scope`, `scope_value`, `key`, `value_type`, `value` | `comment`, `updated_at`, `updated_by` | `config_manager`, hourly gate | IMPORTANT | CONFIRMED |
| `thresholds_partner` | `id`, `enabled`, `analyzer`, `partner`, `metric`, `reason` | thresholds, min_events, … | wallet/hourly rules | IMPORTANT | CONFIRMED |
| `wallet_limits` | `id`, `enabled`, `analyzers`, `scope`, `scope_value`, `limit_type`, `limit_value`, `reason` | `comment`, `method`, … | wallet analyzer | IMPORTANT | CONFIRMED |
| `partner_groups` | `id`, `enabled`, `analyzers`, `group_name`, `partner` | `default_method`, `group_priority`, … | wallet/hourly grouping | IMPORTANT | CONFIRMED |
| `ui_layout` | `id`, `enabled`, `view`, `section`, `order`, `key` | `title`, `style`, `notes` | `wallet_reporter`, `hourly_reporter` | IMPORTANT | CONFIRMED |
| `hourly_payins` | `display_name` | `group_code`, `source_partners`, … | `hourly_analyzer` | IMPORTANT | CONFIRMED |
| `hourly_payouts` | `group_code`, `enabled`, `sort_order` | `display_name`, … | `hourly_analyzer` | IMPORTANT | CONFIRMED |
| `hourly_payout_methods` | `group_code`, `enabled`, `sort_order` | `method_code`, … | `hourly_analyzer` | IMPORTANT | CONFIRMED |
| `payout_info_rules` | `id`, `enabled`, `info_phrase`, `threshold`, `reason` | — | `payout_config_loader.py`, `payout_rules_accessor.py` | OPTIONAL | CONFIRMED |
| `payout_ignore_phrases` | `id`, `enabled`, `info_phrase`, `reason` | — | same | OPTIONAL | CONFIRMED |

**REQUIRED_SHEETS** (отсутствие = structural fail): `meta`, `exclude_time`, `access`, `commands` — `contract_schema.py` L251–253.

**Migration policy (E-CONFIG-02):** `rules.xlsx` — конечная точка миграции config → Rules V2. Новые листы/строки в **production** workbook добавляются только после завершения всех этапов CONFIG-MIGRATION-PHASE-* и подтверждения готовности к runtime switch. Во время фаз разрешены изменения schema/accessors/shadow/flags в коде; test fixtures (`tests/fixtures/payout/`) не являются prod workbook.

Row-level validation rules: **UNKNOWN** (см. `validators.py`, не полностью inventory).

### 3.2 Hourly input (`/tmp/hourly/payin.xlsx`, `payout.xlsx`)

| Column | Обязательна | Optional | Consumer | Статус |
|--------|-------------|----------|----------|--------|
| `Партнер` | да | — | `hourly_analyzer.py` L286–294 | CONFIRMED |
| `Сумма` | да | — | same | CONFIRMED |
| `Статус` | да | — | same (filter `оплачен`) | CONFIRMED |
| `Дата/Время создания` | да | — | same | CONFIRMED |
| `enum метод` / `метод` / `method` / … | нет | payout only | `_find_col` L290 | CONFIRMED |

### 3.3 Wallet input (payin/payout exports)

| File | Required columns (flex match) | Consumer | Статус |
|------|------------------------------|----------|--------|
| payin xlsx | `дата/время создания`, `партнер`, `статус`, `инфо`, `сумма` (aliases via `_find_col`) | `wallet_analyzer.py` L462–471 | CONFIRMED |
| payout xlsx | `partner`/`партнер`, `amount`/`сумма`; optional method column | `wallet_analyzer.py` L267–274 | CONFIRMED |

### 3.4 Conversion input (explicit code mapping)

Column mapping for conversion loads — `main.py` `CONVERSION_COLUMNS` and `analyzers/conversion.py` `load_data` (E-CONFIG-03: no YAML routing config):

| Key | Expected header | Required in `load_data` | Статус |
|-----|-----------------|-------------------------|--------|
| `card` | `Карта` | да (with status, datetime) | CONFIRMED |
| `status` | `Статус` | да | CONFIRMED |
| `datetime` | `Дата/Время создания` | да | CONFIRMED |
| `partner` | `Партнёр` (`main.py`) | mapped if present | CONFIRMED |

### 3.5 Payout input

| File | Required columns | Optional | Consumer | Статус |
|------|------------------|----------|----------|--------|
| payout xlsx | `статус`, `инфо`, `дата/время создания`, `телефон`, `выделено`, `карта` | — | `payout.py` L120 | CONFIRMED |
| cd xlsx | `карта`, `направление` | — | filter IN cards L86–92 | CONFIRMED |

Statuses used: `ошибка`, `оплачен` — L107.

### 3.6 `special_cards.xlsx` (Dropbox optional)

| Column | Required | Consumer | Статус |
|--------|----------|----------|--------|
| `Карта` → `card` | да (for rules) | `conversion.py` L225–235 | CONFIRMED |
| `Партнер` → `partner` | да | same | CONFIRMED |
| `Дата` → `start_date` | да | format `%d.%m.%Y` | CONFIRMED |

### 3.7 DORMANT: `transactions` analyzer

Not in active routing; hardcoded fallback column `Amount` — **DORMANT**, not active contract. Still references removed `analysis_map.yaml` on import — activation requires code fix.

---

## 4. CSV contracts

| Context | Required fields | Module | Критичность | Статус |
|---------|-----------------|--------|-------------|--------|
| Conversion/payout via `load_data` | Columns from `col_mapping` / `CONVERSION_COLUMNS` (`card`, `status`, `datetime`, …) | `conversion.py` L53–71 | IMPORTANT (if CSV input used) | CONFIRMED |
| Encoding | `utf-8`; sep auto (`python` engine) | `conversion.py` L61 | — | CONFIRMED |
| DORMANT `transactions` | column from yaml `transactions.column` default `Amount` | `transactions.py` | — | DORMANT |

Active prod path uses **xlsx** from Antares/Dropbox; CSV support exists in code but prod usage — **UNKNOWN**.

---

## 5. YAML contracts

### `config/payout_config.yaml`

| Section | Required keys | Consumer | Критичность | Статус |
|---------|---------------|----------|-------------|--------|
| `PayoutsErrors` | error phrase → `{threshold: int}` | `payout_config_loader.py` → `payout.py` | IMPORTANT | CONFIRMED |
| `IgnoreErrors` | list of phrases | same | IMPORTANT | CONFIRMED |

Rules V2 sheets ``payout_info_rules`` / ``payout_ignore_phrases`` mirror the same semantics when ``PAYOUT_CONFIG_FROM_RULES_V2=1`` (see env table §1). Production rows — по E-CONFIG-02, после завершения всех migration phases.

### Raccoon Wallet configuration (Rules V2 only)

**Source of truth:** `rules.xlsx` — no legacy YAML config file; no feature flag.

| Config area | Consumer | Rules V2 source | Статус |
|-------------|----------|-----------------|--------|
| Scalars (`window_minutes`, `offset_minutes`, `min_events`, `pending_payin_minutes`, `payin_days_back`) | analyzer/downloader via loader | `job_params` (`job=raccoon_wallet`) | IMPORTANT | CONFIRMED |
| Partner roster | analyzer whitelist loop | roster union: `thresholds_partner` + `wallet_limits` + `partner_groups` | IMPORTANT | CONFIRMED |
| Group membership | analyzer | `partner_groups` sheet | IMPORTANT | CONFIRMED |
| PayIn column mapping | analyzer | **code constants** (`analyzers/raccoon_wallet_columns.py`) | IMPORTANT | CONFIRMED |
| Thresholds / limits / exclude overlay | analyzer | `thresholds_partner`, `wallet_limits`, `exclude_time` | IMPORTANT | CONFIRMED |

**Roster union:** enabled `thresholds_partner` (`analyzer=raccoon_wallet`) + `wallet_limits` (`scope=partner`, `analyzers` ∋ `raccoon_wallet`) + `partner_groups` (`analyzers` ∋ `raccoon_wallet`). Builder: `core/rules_v2/raccoon_wallet_rules_accessor.py`.

**API-cancel detection:** hardcoded substring `API_CANCEL_INFO_KEYWORD` in `raccoon_wallet_analyzer.py` (not YAML-configurable).

---

## 6. JSON contracts

### `state.json` (Dropbox + local cache)

| Path | Type | Critical fields | Consumers | Критичность | Статус |
|------|------|-----------------|-----------|-------------|--------|
| `jobs.{job_type}.last_fingerprint` | string | hourly, wallet dedup | `state_get`/`state_update` | IMPORTANT | CONFIRMED |
| `jobs.{job_type}.last_sent_ts` | int | hourly, wallet | wrappers | OPTIONAL | CONFIRMED |
| `meta.last_update_ts` | int | audit | `state_update_meta` | OPTIONAL | CONFIRMED |
| `meta.last_update_by` | object | actor dict | same | OPTIONAL | CONFIRMED |

Full job keys inventory — **UNKNOWN** (only confirmed keys above).

### Event log JSONL (`events_YYYY-MM-DD.jsonl`)

| Field | Required | Consumer | Статус |
|-------|----------|----------|--------|
| `ts`, `type`, `job_id`, `job_type`, `actor`, `rules_version`, `rules_source`, `payload`, `pid` | `ts`, `type` always | `event_log.py` L63–73 | CONFIRMED |

Event types (non-exhaustive): `job_requested`, `job_started`, `job_finished`, `job_failed`, `job_rejected_busy`, `job_skipped_no_changes`, `job_skipped_missing_inputs`, `state_updated`, `rules_changed_during_job`.

### `rules_identity_registry.v1.json`

| Field | Required | Consumer | Статус |
|-------|----------|----------|--------|
| `schema_version` | `rules_identity_registry.v1` | `identity_registry_io.py` L22 | CONFIRMED |
| rows payload | UNKNOWN (full schema) | identity compare on publish | partial |

### Playwright `storage_state` JSON

| Path | Consumer | Статус |
|------|----------|--------|
| `/tmp/auth_state.json` | `downloader.py` | CONFIRMED (format = Playwright) |
| `/tmp/auth_state_wallets.json` | `downloader_wallets.py` | CONFIRMED |
| `/tmp/hourly/auth_state.json` | `hourly_downloader.py` | CONFIRMED |
| `/tmp/auth_state_wallet_editor_<PROFILE>.json` | `automation/engine.py` (WalletEditor per operator) | CONFIRMED |

Internal Playwright schema — **UNKNOWN** (opaque to app).

---

## 7. Data flow contracts

| # | Source | Transform | Consumer | Output | Критичность | Статус |
|---|--------|-----------|----------|--------|-------------|--------|
| F1 | Dropbox `rules.xlsx` | sync → snapshot/indexes | schedules, access, job_params, analyzers | runtime config | CRITICAL | CONFIRMED |
| F2 | Antares (Playwright) | download xlsx | `/tmp/hourly/` | `hourly_analyzer` → `hourly_reporter` → Telegram | IMPORTANT | CONFIRMED |
| F3 | Antares (Playwright) | download payin/payout | `wallet_analyzer` → `wallet_reporter` → Telegram | wallet job | IMPORTANT | CONFIRMED |
| F4 | Antares (Playwright) | download → Dropbox upload | `main.process_file` → conversion/payout analyzers → Telegram + report xlsx | download job | IMPORTANT | CONFIRMED |
| F5 | Dropbox input files | `selector.get_analyzer` (code constants) + analyzer | conversion/payout modules | TG + processed move | IMPORTANT | CONFIRMED |
| F6 | Bakai web | Playwright scrape | rate compare → Telegram | rate job | IMPORTANT | CONFIRMED |
| F7 | `state.json` fingerprints | sha256 file meta | skip unchanged hourly/wallet | reduced noise | OPTIONAL | CONFIRMED |
| F9 | `config/payout_config.yaml` | error phrase match | `payout.py` filtering | IN/check lists → TG | IMPORTANT | CONFIRMED |
| F10 | Dropbox `special_cards.xlsx` | optional merge | `conversion.py` special rules | filtered conversion | OPTIONAL | CONFIRMED |
| F11 | Telegram `.xlsx` document | allowlist + operator map → `WalletEditorTask` | `automation/engine.py` → Antares UI | result xlsx → Telegram | IMPORTANT | CONFIRMED |
| F12 | `ConversionAnalyzer.problem_cards` | bridge builds Excel (`card`, `action`, `value`) → `WalletEditorTask` | `integrations/conversion_wallet_editor_bridge.py` → `automation/worker.py` → Antares UI | rollout info TG + result xlsx → `CONVERSION_WALLET_EDITOR` chat | OPTIONAL | CONFIRMED |

---

## 8. Locks & fingerprints (data contracts)

| Contract | Location | Semantics | Stale / TTL | Критичность | Статус |
|----------|----------|-----------|-------------|-------------|--------|
| Job lock | `{STATE_DIR}/locks/{job_type}.lock` | PID single-flight | stale if PID dead | IMPORTANT | CONFIRMED |
| Dropbox pipeline lock | `/tmp/dropbox_pipeline.lock` | PID single-flight | 600 sec (`run_once_guard`) | IMPORTANT | CONFIRMED |
| Hourly fingerprint | file meta sha256 + `state.jobs.hourly.last_fingerprint` | skip unchanged | — | OPTIONAL | CONFIRMED |
| Wallet fingerprint | payin+payout meta hash + state | skip unchanged | — | OPTIONAL | CONFIRMED |
| Bakai rate | `/tmp/bakai_last_buy_rate.txt` | last buy rate float | — | OPTIONAL | CONFIRMED |

---

## Remaining UNKNOWN

- Prod row content в `rules.xlsx` (schedules, access rows)
- Полная схема `rules_identity_registry.v1.json` rows
- CSV inputs in prod (code supports; path unverified)
- `DROPBOX_RULES_PATH` / `RULES_LOCAL_PATH` — legacy path usage vs `rules_provider` primary path
- Raccoon data source (no runtime)
- All optional `rules.xlsx` sheet rows semantics at row level
- Exact Antares export column sets beyond code-required minimums
- Partner column spelling: `Партнер` vs `Партнёр` across files (normalization handles partially)

---

## STALE_RISK (contracts)

| ID | Расхождение | Severity |
|----|-------------|----------|
| S1 | Dual lock systems (`/tmp/dropbox_pipeline.lock` vs `{STATE_DIR}/locks/`) | med |
| S2 | Raccoon in docs, absent in code | low |
| S3 | `main.CONVERSION_COLUMNS` partner `Партнёр` vs yaml `Партнер` | low |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-31 | G1 — TASK-2026-05-31-01 |
| 2026-05-31 | G2 partial — TASK-2026-05-31-02 |
| 2026-05-31 | **G5 closed (material)** — TASK-2026-05-31-03 |
| 2026-06-01 | WalletEditor env + file contracts (WE-0…WE-6) |
| 2026-06-01 | Telegram sender health env (Option B) |
