# План выделения Wallet Editor document ingest (TASK-2026-09-17-12)

| Мета | Значение |
|------|----------|
| **Статус** | review пройден (HEAD `428d50fe…`); **не** runtime; merge нет |
| **Репозиторий** | `deniskotdavydov1991-wq/Test` |
| **Обследованный SHA** | `8e57e49f2b04c919680918c3d704136032015bdc` (TASK-11 docs close; код ingest = тот же, что на ветке TASK-11) |
| **Основной источник** | `integrations/wallet_editor_tg.py` |
| **Регистрация mixed** | `integrations/tg_commands.py` `get_handlers()` → `MessageHandler(filters.Document.ALL, handle_wallet_editor_document)` |
| **Эталон mixed команд** | `tests/fixtures/behavior_baseline/expected_tg_commands.json` (**не** менять) |

Это обследование **исходников**, не production и не прогон worker. Isolated Antares entry **ещё нет**. Durable inbox / `JOB_ACCEPT` **не** входят.

Document callback **уже** живёт в отдельном integrations-модуле. В `tg_commands` — только импорт и регистрация `MessageHandler`.

---

## 1. Карта зависимостей и вызовов

### 1.1 Реализация (`integrations/wallet_editor_tg.py`)

| Символ | Роль |
|--------|------|
| `handle_wallet_editor_document` | async callback ingest |
| `parse_allowed_chat_ids` | парсинг `WALLET_EDITOR_ALLOWED_CHAT_IDS` |
| `is_wallet_editor_chat_allowed` | fail-closed membership |
| `log_wallet_editor_allowlist_startup_warning` | один раз за процесс: warning если allowlist пуст, иначе info со списком |
| `is_xlsx_file_name` | суффикс `.xlsx` (case-insensitive) |
| `_telegram_user_id` | `effective_user.id`, иначе `message.from_user.id` |
| `TMP_DIR` | `Path("/tmp/wallet_editor")` |
| `_ALLOWLIST_STARTUP_LOGGED` | флаг однократного startup log |
| module-level call | `log_wallet_editor_allowlist_startup_warning()` при **импорте** модуля |

Логгер ingest: `automation.audit.log`, **не** `bind_logger` Antares handlers и **не** `integrations.tg_commands.log`.

Доступ: чат-allowlist + `resolve_operator_for_user`. **Не** `check_access` / `_guard_or_deny` / command ACL.

### 1.2 Импорт-time зависимости сейчас

При `import integrations.wallet_editor_tg` сразу загружаются:

- `automation.audit.log`
- `automation.runtime` (типы заданий, `resolve_operator_for_user`, dry_run flag, тексты MSG_*)
- `automation.edit_wallet_contract.detect_excel_routing` (и транзитивно pandas/Excel)
- `automation.worker.add_task` / `add_add_wallet_task` / `add_edit_wallet_task`

Затем выполняется startup warning. Worker **не** стартует от этого импорта: `ensure_worker_started()` — отдельный вызов в `scheduler.py` (lazy per-profile).

### 1.3 Регистрация mixed

`integrations/tg_commands.py` (обследованный SHA):

- импорт: `from integrations.wallet_editor_tg import handle_wallet_editor_document`
- последний элемент `get_handlers()`: `MessageHandler(filters.Document.ALL, handle_wallet_editor_document)`

Обратного импорта `wallet_editor_tg` → `tg_commands` **нет**.

### 1.4 Scheduler

`scheduler.py` **не** импортирует `wallet_editor_tg` напрямую.

Цепочка: `from integrations.tg_commands import get_handlers, RULES` → импорт `wallet_editor_tg` → startup warning.

Позже: `ensure_worker_started()` (один раз). Это **не** ingest callback.

### 1.5 Harness

| Файл | Что |
|------|-----|
| `tests/unit/registration_harness/sitecustomize.py` | stub `integrations.wallet_editor_tg.handle_wallet_editor_document` = blocked |
| `tests/unit/legacy_scheduler_harness/sitecustomize.py` | то же |

Пока `tg_commands` импортирует `wallet_editor_tg`, harness может не загружать реальный ingest. Это **не** доказательство работы ingest.

### 1.6 Тестовые patch-пути (фактические)

Все callback-патчи сегодня на **`integrations.wallet_editor_tg.*`**, потому что `add_task` / `add_add_wallet_task` / `is_wallet_editor_chat_allowed` связаны в этом модуле:

- `integrations.wallet_editor_tg.add_task`
- `integrations.wallet_editor_tg.add_add_wallet_task`
- `integrations.wallet_editor_tg.is_wallet_editor_chat_allowed`
- `integrations.wallet_editor_tg.parse_allowed_chat_ids`
- env `WALLET_EDITOR_ALLOWED_CHAT_IDS` / operator map
- AST/source grep: `integrations/wallet_editor_tg.py` без `WALLET_EDITOR_ANTARES_LOGIN` (`test_wallet_editor_tg_integration`, `test_wallet_editor_credentials`)

`add_edit_wallet_task` в ingest-тестах **не** патчится: ветки EDIT_WALLET нет.

---

## 2. Фактический контракт callback

Порядок в `handle_wallet_editor_document` (SHA `8e57e49…`):

1. Нет `update.message` или нет `message.document` → **тихий return** (нет reply, нет download, нет enqueue).
2. `chat_id = int(update.effective_chat.id)`.
3. Allowlist: пустой или chat не в множестве → log info `chat denied` → reply `⛔ Чат не разрешён для WalletEditor.` → return. Download/enqueue нет.
4. Имя файла не `.xlsx` (пустое / иное расширение) → log → `❌ Принимаются только файлы .xlsx` → return. `get_file` нет.
5. `telegram_user_id`: `effective_user`, иначе `message.from_user`. Если оба отсутствуют → warning → reply `MSG_OPERATOR_UNMAPPED` (`⛔ Для вашего Telegram user_id не настроен профиль WalletEditor.`) → return.
6. `resolve_operator_for_user(telegram_user_id)`: нет профиля / неполные credentials → reply `error_message` или `MSG_OPERATOR_UNMAPPED` / `MSG_OPERATOR_INCOMPLETE` → return. Download нет.
7. **Дальше try:**
   - reply `📥 Файл получен`
   - `_ensure_tmp_dir()` → `TMP_DIR.mkdir`
   - путь `TMP_DIR / f"wallet_editor_{uuid4().hex}.xlsx"`
   - `context.bot.get_file(document.file_id)` + `download_to_drive(custom_path=str(local_path))`
   - `detect_excel_routing(str(local_path), original_filename=document.file_name)`
8. `ExcelRouting.AMBIGUOUS` → log → `local_path.unlink(missing_ok=True)` (OSError глотается) → reply `❌ Не удалось определить тип Excel: {routing_error or 'ambiguous'}` → return. Enqueue нет.
9. `ADD_WALLET` → `add_add_wallet_task(WalletEditorAddWalletTask(...))` с `dry_run=wallet_editor_add_wallet_dry_run_enabled()` → reply Add Wallet + `queue_size` + `dry_run=...` → return.
10. `EDIT_WALLET` → `add_edit_wallet_task(WalletEditorEditWalletTask(...))` → reply Edit Wallet + очередь → return.
11. **Иначе** (включая `ExcelRouting.DISABLE` и любой иной не-ADD/не-EDIT) → `add_task(WalletEditorTask(...))` → reply `📌 Файл добавлен в очередь профиля {profile}. Текущий размер очереди: {queue_size}`.
12. Любое исключение **внутри try** (после прохождения allowlist/xlsx/operator): `log.exception` → `❌ Ошибка при приёме файла: {e}`. Временный файл **не** удаляется в этом except (в отличие от AMBIGUOUS).

Стартовый ответ, создание пути и download — **внутри** try. Allowlist / xlsx / operator — **снаружи** try.

`bind_rules` / `bind_logger` **не** применяются и в code PR **не** добавляются: политика — chat allowlist + operator map; лог — `automation.audit.log`.

Command ACL (`auto_enable_*` и т.д.) **не** заменяет allowlist.

### 2.1 Поля заданий (фиктивные credentials)

Примеры значений: `login="operator-login-example"`, `password="operator-pass-example"`, `profile_key="DENIS"`, `chat_id=-1000000000001`, `user_id=123456789`. Не использовать боевые кабинеты.

**`WalletEditorAddWalletTask`** (`add_add_wallet_task`):

| Поле | Источник |
|------|----------|
| `file_path` | локальный tmp `.xlsx` |
| `original_filename` | `document.file_name` или `"input.xlsx"` |
| `operator_profile` | `operator.profile_key` |
| `chat_id` | chat ingest |
| `user_id` | `telegram_user_id` |
| `login` / `password` / `auth_state_path` | operator profile |
| `dry_run` | env add-wallet dry_run |
| `requires_manual_snapshot_gate` | default dataclass (`False`) |
| `created_at` / `queued_at` | default factory |

**`WalletEditorEditWalletTask`** (`add_edit_wallet_task`): те же file/operator/chat/user/credentials поля, **без** `dry_run`. Defaults: `created_at`, `queued_at`.

**`WalletEditorTask`** (fallback / DISABLE):

| Поле | Источник |
|------|----------|
| `file_path` | tmp |
| `chat_id` | chat ingest |
| `telegram_user_id` | sender |
| `operator_profile` | profile_key |
| `source_file_name` | file_name или `"input.xlsx"` |
| `login` / `password` / `auth_state_path` | operator |
| `run_id` / `manual_snapshot_binding` / `queued_at` | defaults |

Файл на диске после успешного enqueue **остаётся** для worker. После AMBIGUOUS — удаляется. Это **не** durable inbox.

Различие, которое нельзя смешивать: ответ «Файл получен» + постановка в **текущую in-memory очередь** ≠ будущий durable-приём / `JOB_ACCEPT`.

---

## 3. Минимальная граница переноса

### Решение: владелец реализации — `modules.antares.document_ingest`

Не класть ingest в `modules.antares.handlers`: там command callbacks, `bind_rules`/`bind_logger`, `guard_or_deny`. Ingest — другой контур доступа и другой логгер.

Не оставлять вторую копию тела callback. Не делать forwarding-wrapper «новый путь вызывает старый» как целевую архитектуру: **тело живёт в одном модуле**.

| Слой | После code PR |
|------|----------------|
| `modules.antares.document_ingest` | callback, helpers, **единственный** `TMP_DIR`, **единственный** `_ALLOWLIST_STARTUP_LOGGED`, `log_wallet_editor_allowlist_startup_warning` (единственный import-time вызов) |
| `integrations.wallet_editor_tg` | явный re-export **тех же function objects**: `handle_wallet_editor_document` и совместимые helpers (`parse_allowed_chat_ids`, `is_wallet_editor_chat_allowed`, `is_xlsx_file_name`, `log_wallet_editor_allowlist_startup_warning`). **Не** реэкспортировать `_ALLOWLIST_STARTUP_LOGGED`. **Не** вызывать startup warning повторно |
| `integrations.tg_commands.get_handlers` | по-прежнему импорт **`from integrations.wallet_editor_tg import handle_wallet_editor_document`** и тот же `MessageHandler`. Имя, фильтр, позиция не меняются. Identity: объект из `document_ingest` |
| `scheduler.py` | без изменений: warning срабатывает, потому что `tg_commands` тянет `wallet_editor_tg` → `document_ingest` |
| Harness sitecustomize | stub `integrations.wallet_editor_tg` можно оставить: mixed не обязан грузить `document_ingest`, если импорт идёт через stubbed compat |

Обратного импорта `document_ingest` / `wallet_editor_tg` → `tg_commands` нет.

### 3.1 Единственный владелец состояния

`_ALLOWLIST_STARTUP_LOGGED` существует **только** в `modules.antares.document_ingest`. Compat **не** экспортирует этот флаг, **не** даёт setter и **не** делает alias на модуль-владелец.

Тесты будущего code PR:

- сбрасывают `_ALLOWLIST_STARTUP_LOGGED` **на owner**;
- подменяют `TMP_DIR`, которым пользуется callback, **на owner** (`modules.antares.document_ingest.TMP_DIR`);
- не считают `integrations.wallet_editor_tg._ALLOWLIST_STARTUP_LOGGED = False` сбросом owner.

Присваивание одноимённого атрибута на compat (`wallet_editor_tg._ALLOWLIST_STARTUP_LOGGED = …` или `wallet_editor_tg.TMP_DIR = …`) **не** меняет globals owner и **не** меняет путь, по которому callback пишет файл.

`TMP_DIR`:

- **чтение** старого имени с compat допустимо только как совместимый lookup того же *исходного* объекта, если code PR явно делает `from modules.antares.document_ingest import TMP_DIR` — это снимок имени на момент импорта compat;
- **подмена значения, которое использует callback**, всегда через owner;
- прозрачной синхронизации присваиваний между модулями **нет** и обещать её нельзя: после `wallet_editor_tg.TMP_DIR = tmp_path` callback продолжит видеть `document_ingest.TMP_DIR`.

`wallet_editor_tg` не вызывает `log_wallet_editor_allowlist_startup_warning()` на своей последней строке.

### 3.2 Startup warning: одно место

Единственное решение:

- warning вызывается **при импорте owner** (`document_ingest`);
- compat **сам** warning не вызывает;
- `scheduler.py` **не** меняется.

Сейчас warning стоит в конце `wallet_editor_tg.py` и срабатывает, потому что mixed `tg_commands` (а scheduler — `tg_commands`) импортирует этот модуль. После переноса тот же момент процесса: `tg_commands` → compat → owner → один import-time log.

Импорт только owner (будущий isolated Antares) тоже даёт один log. Отдельный вызов из scheduler не вводится.

`importlib.reload` для проверки startup **не** использовать.

### 3.3 Что можно загружать отложенно (внутри callback, после allowlist/xlsx/operator)

Чтобы `import modules.antares.document_ingest` не открывал Excel/worker:

- `detect_excel_routing`
- `add_task` / `add_add_wallet_task` / `add_edit_wallet_task`
- конструкторы `WalletEditor*Task` и `wallet_editor_add_wallet_dry_run_enabled`

`resolve_operator_for_user` и тексты MSG_* — после allowlist/xlsx; их тоже можно импортировать внутри callback, чтобы import owner не парсил operator map. Тогда `import document_ingest` оставляет: stdlib, telegram types, `automation.audit.log` (для startup warning), env parse allowlist.

**Остаётся на import-time и почему:**

- `automation.audit.log` — startup warning без откладывания «на первый документ» (сейчас warning при старте процесса через `tg_commands`, не при первом xlsx).
- Вызов `log_wallet_editor_allowlist_startup_warning()` — текущий контракт «при загрузке модуля ingest».
- `TMP_DIR` / allowlist helpers — чистые, без I/O кроме чтения env.

**«Чистый импорт» owner** (будущий тест): разрешены существующий `automation.audit.log`, чтение allowlist из env и startup log. Запрещены загрузка worker/routing, запуск потоков, браузер, сеть, БД и чтение рабочих Excel.

Логирование при импорте **не** называть отсутствием всех побочных эффектов.

Import-time **не** должен: ходить в Telegram, PostgreSQL, Playwright, запускать worker, читать operator workbook WalletEditor.

### 3.4 Patch-пути для lazy imports

Внутри callback:

```python
from automation.worker import add_task
```

имя `add_task` **локальное**. Атрибут `modules.antares.document_ingest.add_task` от этого **не** появляется.

Патчить **`automation.worker.add_task` до вызова callback** либо заранее ставить явный stub модуля `automation.worker`. То же для `add_add_wallet_task` / `add_edit_wallet_task`.

Аналогично routing и прочим локальным импортам (`detect_excel_routing`, типы заданий, `resolve_operator_for_user`, dry_run helper): патч на модуль, откуда имя импортируется внутри callback, до входа в callback — либо заранее подменённый модуль.

Глобальные helpers owner (`is_wallet_editor_chat_allowed`, `parse_allowed_chat_ids`, `is_xlsx_file_name`, `log_wallet_editor_allowlist_startup_warning`) патчить как `modules.antares.document_ingest.<name>`.

Тесты, импортирующие `integrations.wallet_editor_tg.handle_wallet_editor_document`, вызывают **тот же** function object, что и owner.

Не оставлять патч `integrations.wallet_editor_tg.add_task`: после переноса это имя в compat не используется callback.

Source-assert «нет `WALLET_EDITOR_ANTARES_LOGIN`» расширить на `document_ingest.py` (compat станет тонким для grep).

---

## 4. Карта проверок для будущего code PR

Регистрация `MessageHandler` и stub enqueue **не** доказывают обработку задания worker и **не** заменяют контракт ingest.

### 4.1 Уже покрыто (`tests/unit/test_wallet_editor_tg_integration.py`)

| Контракт | Тест |
|----------|------|
| parse allowlist empty/values | `test_parse_allowed_chat_ids_*` |
| fail-closed empty / member | `test_is_wallet_editor_chat_allowed_*`, `test_empty_wallet_editor_allowlist_*`, dedicated env vs `TELEGRAM_ALLOWED_CHAT_IDS` |
| startup warning empty allowlist | `test_startup_warning_when_allowlist_empty` (ручной сброс `_ALLOWLIST_STARTUP_LOGGED`) |
| deny chat → нет enqueue | `test_disallowed_chat_rejected*` |
| non-xlsx / empty name → нет `get_file` | `test_non_xlsx_*`, `test_empty_file_name_*` |
| xlsx → `get_file(file_id)` + download | `test_xlsx_calls_get_file_*` |
| fallback DISABLE → `add_task` + поля + «Файл получен» | `test_xlsx_document_queues_task`, `test_mapped_user_queues_task_*`, `test_handler_uses_profile_queue_size_*` |
| ADD_WALLET → `add_add_wallet_task`, не `add_task` | `test_add_wallet_xlsx_routes_to_add_wallet_task` |
| AMBIGUOUS → нет enqueue, текст типа Excel | `test_ambiguous_xlsx_rejected_without_queueing` |
| unmapped / incomplete operator до download | `test_unmapped_user_*`, `test_mapped_user_missing_credentials_*` |
| ingest не вызывает `engine.run` | `test_handler_does_not_call_engine_run_directly` |
| mixed commands + один MessageHandler | `test_existing_telegram_commands_still_registered` |
| import без credentials | `test_import_safe_without_antares_credentials` |

Смежные, **не** ingest callback: scheduler `ensure_worker_started` count, `cmd_status`, operator map normalize — оставить; не считать их проверкой ingest.

Контракт routing Excel сам по себе: `tests/unit/test_edit_wallet_contract.py`, `test_add_wallet_contract.py` — не замена TG enqueue.

### 4.2 Недостающие проверки (сделать в code PR, без живых кабинетов)

- Нет `message` / нет `document` → тихий выход, нет download/enqueue.
- Deny allowlist: нет `get_file` (сейчас часть тестов не проверяет download).
- Fallback `from_user`, если `effective_user` is None.
- **EDIT_WALLET** → только `add_edit_wallet_task`, поля задания, operator profile, не `add_task` / не add-wallet.
- ADD_WALLET: явные поля включая `dry_run` (сейчас проверяется тип и profile, не полный набор).
- AMBIGUOUS: файл **удалён** (`unlink`); enqueue нет.
- Ошибка `get_file` / `download_to_drive` → `❌ Ошибка при приёме файла:` + exception log; enqueue нет.
- Ошибка `detect_excel_routing` (исключение, не AMBIGUOUS return) → тот же except.
- Ошибка enqueue (`add_task` raises) → тот же except; файл не обязан удаляться (текущее поведение).
- **Обязательный отдельный identity-тест** (не harness dump): реальные owner и compat, внешние границы заглушены (worker/routing/Telegram не живые). `document_ingest.handle_wallet_editor_document is wallet_editor_tg.handle_wallet_editor_document`. Если registration harness подменяет `wallet_editor_tg` целиком, его dump доказывает только mixed wiring и `filters.Document.ALL`. Такой dump **не** выдавать за проверку реального re-export.
- `get_handlers()` (вне harness, с реальным compat): callback **is** owner; фильтр `Document.ALL`.
- Startup в **свежем subprocess** (без `importlib.reload`): отдельно `owner → compat` и отдельно `compat → owner`; в каждом процессе ровно один startup log. Повторный вызов `log_wallet_editor_allowlist_startup_warning` в том же процессе log не добавляет.
- Чистый импорт owner: см. § 3.3. Проверять owner, не «отсутствие любых логов».

Не добавлять тесты реального worker / включения партнёра / production-БД / Telegram.

`expected_tg_commands.json` не менять. Durable inbox не тестировать — его нет.

---

## 5. Объём следующего code PR (не этот PR)

Один PR:

1. Добавить `modules/antares/document_ingest.py` с переносом тела и состояния.
2. Сжать `integrations/wallet_editor_tg.py` до identity re-export.
3. Не менять сигнатуру `get_handlers()` / фильтр / порядок.
4. Перенацелить callback-тесты и patch на фактические имена; добавить пробелы из § 4.2.
5. Отдельный identity-тест реальных owner/compat (§ 4.2). Registration dump не расширять до «re-export identity», пока harness подменяет `wallet_editor_tg`.
6. Startup — свежие subprocess § 4.2, не `reload`.
7. Не трогать `automation/worker.py` очереди, engine, contracts Excel, scheduler, профили, `JOB_ACCEPT`.

Вне scope code PR: isolated entry, cutover, Railway, merge #4–#15, document ingest в `handlers.py`, замена allowlist на command ACL, TASK-13 до закрытия этого плана.

---

## 6. Зафиксированные неоднозначности (закрыты)

1. Флаг startup и рабочий `TMP_DIR` — только owner. Compat не экспортирует `_ALLOWLIST_STARTUP_LOGGED`. Присваивание одноимённого атрибута compat не синхронизирует owner.
2. Lazy `from automation.worker import add_task` внутри callback → патч `automation.worker.add_task` (или stub модуля) до вызова. Не `document_ingest.add_task`.
3. Startup warning — только import owner. Scheduler не менять. Альтернатива «вызов из scheduler» снята.
4. Identity re-export — отдельный тест с реальными модулями. Harness dump = wiring + `Document.ALL`, не identity.
5. Harness может stub'ить `wallet_editor_tg`; `document_ingest` в registration dump не обязан грузиться.

Блокирующих UNKNOWN для переноса тела callback нет. TASK-13 не начинать, пока этот план не принят.
