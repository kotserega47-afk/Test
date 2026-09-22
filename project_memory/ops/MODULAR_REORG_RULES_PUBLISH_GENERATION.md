# Согласованная публикация rules snapshot и indexes (TASK-31)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **принят** (review GPT на `8ef2838…`); code TASK-32 review GPT на `bb25f734…`; runtime этого docs PR **не** менялся |
| **База** | TASK-30 HEAD `1eefc54720ccd036451f7c3c0e7dadaedf6efb98` (Draft PR #33, TASK-30 **не** закрыт) |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Repro исходного дефекта** | `test_repro_*` на SHA `1eefc54` (история дефекта, не будущий safety-критерий) |

Контракт **принят**. Code scope — TASK-32. Смысл strict/shadow/legacy и provider stale-reuse **не** менять. Live credentials, merge, deploy, исходное Test, закрытие TASK-30 — вне этого docs PR.

---

## 0. Дефект, который фиксируем (исходники на `1eefc54`)

`core/rules_provider.py`:

- `get_snapshot_v2` пишет `_last_v2_stat`, `_last_v2_snapshot`, `_last_v2_decision` тремя присваиваниями.
- `get_indexes_v2` строит indexes от **локального** `snapshot`, затем `_last_indexes_stat = _last_v2_stat` (уже чужое поколение).
- `get_rules_snapshot` / `_download_rules_workbook_atomic` делает `part.replace(_RULES_LOCAL)` **до** успешной публикации in-memory.
- `_try_save_identity_registry_after_publish` пишет identity registry после `publish_allowed`, без номера поколения.
- `AccessRules.invalidate` **не** вызывает `invalidate_rules_v2_cache`.

`core/access_rules.py` `get_snapshot`: каждый вызов заново зовёт `get_snapshot_v2` + `build_indexes`, затем может заменить `self._snap`. Старый reader, закончивший сборку позже reload, откатывает `_snap`.

`mtime`/`size` файла (`_stat_key`) и `meta.updated_at` **не** номер поколения.

Пара `get_snapshot_v2()` + `get_indexes_v2()` даже после «атомарного» PublishedState может разъехаться между двумя вызовами.

TASK-30 isolated `/reload_rules` **заблокирован** этим дефектом.

---

## 1. Единое опубликованное поколение

Один неизменяемый объект **PublishedState** (имя code PR):

| Поле | Смысл |
|------|--------|
| `generation` | значение process-local **`publish_seq` на момент этого commit** |
| `attempt_id` | id compute, который выиграл commit |
| `snapshot` | `RulesSnapshotV2` |
| `decision` | тот же `SnapshotPublishDecision` |
| `source_path` | исходный путь пользователя / Dropbox-ключ **для диагностики**; не файл parse |
| `capture_path` | неизменяемый файл **этого** поколения, из которого получены snapshot и indexes |
| `canon_path` | канонический cache (`_RULES_LOCAL`) для **следующего запуска** процесса |
| `stat_key` | `(mtime, size)` **capture** на момент parse; не номер поколения |
| `indexes` | `RulesIndexes` из **того же** compute (eager, без lazy subset-update) |
| `policy_mode` | `legacy` / `strict` / `shadow` на момент commit |

Публикация — **одно** присваивание ссылки на новый объект. Запрещено менять поля уже видимого поколения.

Продвижение канона **не** перемещает и **не** удаляет `capture_path`. Reader старого поколения продолжает открывать **свой** capture после нового commit и после invalidate.

### 1.1 Счётчик `publish_seq` (анти-ABA)

Отдельный process-local счётчик успешных публикаций:

- стартует с 0 в процессе;
- **никогда не сбрасывается** при `invalidate_rules_v2_cache` / пустом PublishedState;
- каждая **успешная** публикация: `publish_seq += 1`, новый объект несёт это значение как `generation`.

Invalidate **очищает ссылку** PublishedState. Следующий commit получает новое `generation`, **не** повторный `1`.

`latest_attempt` и `invalidate_epoch` тоже не обнуляются. `mtime`/`stat` их не заменяют.

---

## 2. Согласованный read API

**Не обещаем**, что два независимых вызова `get_snapshot_v2()` и `get_indexes_v2()` относятся к одному поколению.

Единый accessor (имя code PR, например `get_published_state()`):

- сначала **проверка актуальности источника** (§3.0);
- cache hit — только если источник актуален **и** не `force_sync`;
- иначе compute §5;
- один объект: snapshot + decision + пути workbook + indexes + `generation`.

Существующие `get_snapshot_v2` / `get_indexes_v2` / `get_rules_snapshot` остаются. Каждый возвращает поле **какого-то** поколения на момент вызова, без пары. Callers, которым нужна согласованная пара, **обязаны** перейти на accessor.

### 2.1 Callers, которым нужна пара (code scope)

| Файл | Вызовы |
|------|--------|
| `integrations/wallet_editor_registry_settings.py` | `get_snapshot_v2` + `get_indexes_v2` |
| `integrations/wallet_editor_registry_refresh.py` | то же |
| `integrations/wallet_editor_auto_enable_settings.py` | то же |
| `integrations/wallet_editor_auto_enable.py` | то же |
| `analyzers/wallet_analyzer.py` | то же |
| `analyzers/hourly_analyzer.py` | то же |
| `core/access_rules.py` | `get_snapshot_v2` + свой `build_indexes` → indexes поколения |

### 2.2 Callers одного поля

| Файл | Что берут |
|------|-----------|
| `core/schedules.py`, `integrations/telegram_routes.py`, `integrations/conversion_fingerprint.py`, `analyzers/conversion.py`, `reporters/hourly_reporter.py`, `reporters/hourly_render_model.py` | snapshot |
| `core/job_runner.py` | `get_rules_snapshot` затем отдельно `get_snapshot_v2` — **не пара** |
| workbook path: `core/config_manager.py`, `integrations/wallet_editor_partner_resolve.py`, `analyzers/raccoon_wallet_analyzer.py`, `analyzers/raccoon_hourly_report.py`, `analyzers/raccoon_wallet_config_loader.py`, `analyzers/payout_config_loader.py`, `core/rules_v2/ops_rules_validate_summary.py` | `get_rules_snapshot` / `.local_path` |

`get_indexes_v2` после исправления: поле **текущего** accessor-вызова, не склейка с предыдущим `get_snapshot_v2`.

Совместимость `.local_path` — §6.4.

---

## 3. AccessRules: freshness, затем generation + epoch

Только instance lock недостаточен. Сравнение двух локальных ссылок (`_snap` и копия `published`) **недостаточно**: обе могут хранить одно **устаревшее** поколение, пока исходник/TTL/policy уже другие.

Порядок блокировок **всегда** `provider lock → instance lock`. Никогда наоборот.

Provider lock **не** держат на download, parse, `evaluate_snapshot_publish`, `build_indexes`, записи **тела** temp/capture. **Короткий `os.replace` канона (workbook и identity) разрешён в той же критической секции, что commit и invalidate.** Admission lock не участвует.

`invalidate()`: под instance lock `_snap_epoch += 1`, `_snap = None`. `publish_seq` не трогает.

### 3.0 Freshness источника (accessor, до reuse)

`get_published_state(force_sync=)` **до** сравнения generation у AccessRules:

| Проба | Hit только если |
|-------|-----------------|
| `force_sync=True` | никогда (compute) |
| локальный файл `RULES_XLSX_PATH` | `_stat_key(исходник)` == `stat_key` capture текущего PublishedState |
| remote / Dropbox | не истёк TTL (`RULES_SYNC_MIN_INTERVAL_SEC`) в смысле **нынешнего** `get_rules_snapshot` |
| `policy_mode` | равен `resolve_contract_validation_mode().value` |

Обычный hit **не** стартует attempt и **не** делает force reload.

Не hit (файл сменили без `invalidate`, TTL, смена policy) → compute §5. `stat` — проба свежести, не `generation`.

### 3.1 Начало вызова AccessRules

1. `P = get_published_state(force_sync)` — уже freshness §3.0.
2. Под provider затем instance: reuse `_snap` только если он есть, `provider_generation == P.generation`, epoch совпал.
3. Иначе производный снимок из **этого** `P`, затем CAS §3.2.

### 3.2 CAS записи `_snap`

В начале: `start_epoch = _snap_epoch`. Собрать `D` из `P` локально.

```text
lock provider
  current = published
  lock instance
    if _snap_epoch != start_epoch: do_not_store
    elif current is None or current.generation != P.generation: do_not_store
    else: _snap = D
  unlock instance
unlock provider
return D
```

Старый reader возвращает свой `D`, даже при `do_not_store`. Не записывает его поверх нового `_snap`.

---

## 4. Provider outcome vs команда reload

Публичные `get_snapshot_v2` / `get_indexes_v2` / `get_rules_snapshot`: типы и **legacy stale-reuse при ошибке загрузки/сборки** как сейчас. Смысл policy не менять.

| Код | Когда | Память | Публичный API |
|-----|--------|--------|----------------|
| `fresh_commit` | этот attempt выиграл CAS | новый PublishedState, `publish_seq` вырос | вернуть snapshot |
| `existing` | freshness-hit **или** проигрыш attempt при уже **более новом** поколении, чем `observed_generation` | не этот attempt | вернуть snapshot текущего поколения |
| `stale_reuse` | **только** legacy: этот compute не собрал snapshot (build/load fail), отдан прошлый PublishedState | поколение **не** выросло | вернуть прошлый snapshot, **без** исключения (как сейчас) |
| `rejected` | `ContractPublishRejected` | без commit | **исключение** `ContractPublishRejected` |
| `conflict_exhausted` | три неуспешных force-attempt (§5.4), **не** legacy build/load fail | без commit этим вызовом; прежний PublishedState не выдаётся как успех force | **исключение** `RulesPublishConflictExhausted` (новый тип, не `ContractPublishRejected`) |

`stale_reuse` ≠ `conflict_exhausted`. Первое — ошибка разбора workbook при живом прошлом поколении. Второе — гонка attempt/invalidate, исчерпан лимит.

`existing` при чужом `fresh_commit` — не `stale_reuse`.

### 4.1 `/reload_rules`

Mixed callback **не** менять молча: нет исключения из `get_snapshot(force_sync=True)` ⇒ reset + «перечитан» (включая `stale_reuse`). Исключение (`ContractPublishRejected` **или** `RulesPublishConflictExhausted`) ⇒ warning, без reset.

Isolated (метаданные / узкий helper, публичный provider не ломать):

| Исход | Isolated reset | Ответ |
|-------|----------------|--------|
| `fresh_commit` | да | «перечитан» |
| `existing` (поколение новее старта команды) | да | «перечитан», source того поколения |
| `stale_reuse` | нет | warning, не reload |
| `rejected` / `conflict_exhausted` | нет | warning по исключению |

Откат `AccessRules.invalidate` не обещается.

---

## 5. Compute вне lock (attempt)

```text
under lock:
  my_attempt = ++latest_attempt
  start_epoch = invalidate_epoch
  observed_generation = published.generation if published else None
unlock

# вне lock: только подготовка содержимого
build immutable capture file (copy/download into this process's capture dir)
parse capture; evaluate; eager build_indexes
buffer audit
if identity save allowed for this evaluate result:
    write identity temp *body* from evaluate (not canon replace)
# иначе identity_tmp отсутствует — replace identity в секции commit не вызывать

under lock:                    # та же секция, что invalidate
  if invalidate_epoch != start_epoch: discard
  elif my_attempt != latest_attempt: discard
  else:
    publish_seq += 1
    published = PublishedState(generation=publish_seq, attempt_id=my_attempt,
                               capture_path=..., source_path=..., canon_path=...)
    os.replace(canon_tmp, canon_path)      # короткий; см. ошибки §6.2
    if identity_tmp is not None:
        os.replace(identity_tmp, identity_canon)
unlock

if discarded: audit discard; unpublished capture: §6.3
```

`observed_generation` читается **только** в этой регистрации attempt, вместе с `latest_attempt` и `invalidate_epoch`. `None` — пусто, не «0».

`generation` нового объекта — `publish_seq` после инкремента.

Тело capture/identity temp — **вне** lock. `os.replace` канона — **внутри** той же секции, что commit. Проверка *после* успешного replace **не** отменяет уже совершённую перезапись: актуальность должна быть известна **до** `replace`.

### 5.1 Два force, обратный финиш

Побеждает последний стартовавший с `my_attempt == latest_attempt`. Ранний, вошедший в lock позже, discard, **канон не трогает**.

Тест G17: старый writer **остановлен между** готовностью capture/temp и входом в секцию; новый commit побеждает; канон меняет только победитель; старый после пробуждения не делает `replace`.

### 5.2 `conflict_exhausted`

После **трёх** полных attempt одного публичного `force_sync=True` (и freshness-miss, который ушёл в compute), если ни один не сделал `fresh_commit`:

- память: этот вызов ничего не опубликовал;
- **не** возвращать прежний snapshot как успех force;
- **не** классифицировать как `stale_reuse`;
- поднять `RulesPublishConflictExhausted`;
- isolated и mixed: ветка исключения (warning, без reset).

Если на одной из попыток чужой attempt уже сделал `fresh_commit` и freshness удовлетворена — это `existing`, исключение **не** нужно, счётчик попыток обрывается успехом чтения.

### 5.3 Invalidate во время compute

`invalidate_epoch++`, `latest_attempt++`, `published = None`. `publish_seq` не трогать. Опубликованные capture **не** удалять. Identity/canon `replace` не выполнять. Audit **discard**. Неопубликованный capture этого attempt — §6.3.

### 5.4 Лимит попыток

Не более **3** attempt на один публичный вызов, который не hit. `rejected` не ретраить. Не `while True`.

---

## 6. Capture, канон, `get_rules_snapshot`

Источник истины **в процессе** — PublishedState + его `capture_path`. Канон на диске — best-effort для следующего процесса. Атомарности «память + несколько файлов» нет, но **канон не меняется без удержания provider lock и проверки attempt**.

Kill процесса ≠ `OSError` replace: после kill следующее чтение с канона.

### 6.1 Три пути

| Имя | Смысл |
|-----|--------|
| `source_path` | env `RULES_XLSX_PATH` / Dropbox-ключ; диагностика; пользовательский файл **не** заменяем |
| `capture_path` | уникальный неизменяемый файл поколения; parse только его |
| `canon_path` | `/tmp/rules_cache/rules.xlsx` (как `_RULES_LOCAL`); обновляется **отдельным** temp+`replace`, capture не relocaten и не удаляется этим шагом |

Локальный исходник: на attempt копируется в capture; правка пользователем во время parse в поколение не входит.

### 6.2 Replace под той же секцией, что commit

Подготовка `canon_tmp` (копия байт capture) — вне lock. `identity_tmp` — только после evaluate и только если identity save разрешён (§5); иначе в секции commit identity `replace` нет.

В секции commit, **после** проверки attempt, **до** unlock:

| Файл | Успех | `OSError` replace |
|------|--------|-------------------|
| workbook canon | канон = содержимое этого commit | память **не** откатывать; `capture_path` жив; канон мог остаться **предыдущим**; лог; этот вызов не ретраит replace |
| identity canon | канон identity = temp этого attempt | как сейчас ignored для in-memory; не оставлять частично дописанный канон (только complete temp+replace). Память не откатывать |

Если проверка attempt провалилась — **ни одного** `replace`. Поздний identity/workbook writer не затирает канон победителя.

Не делать `replace` после unlock с повторной проверкой: она не отменит уже записанный канон.

### 6.3 Lifetime capture (первое исправление)

Правило `G < publish_seq - 1` **запрещено**. Опубликованный capture **не** удаляют из-за того, что поколение перестало быть текущим.

Для **первого** code:

- каталог capture **уникален для этого запуска процесса** (например suffix pid+start-token под cache root); не чистить и не reuse каталоги других потенциально живых процессов;
- каждый опубликованный capture живёт **до конца процесса**;
- `commit` и `invalidate` опубликованные файлы **не** удаляют;
- проигравший **неопубликованный** capture можно удалить, только если путь **не** отдан reader (не попал в возвращённый PublishedState / `local_path`) и больше не используется этим attempt;
- **не** вводить reader leases / refcount в этом исправлении;
- очистка опубликованных capture (в т.ч. между процессами, по возрасту, по диску) — **отдельный будущий scope**.

Ограничение: объём файлов растёт с числом опубликованных поколений. Это компромисс первого исправления, **не** политика хранения для бессрочного production-сервиса.

Гарантия reader: сохранив `local_path` / `capture_path` поколения G, после любых последующих commit и invalidate в **этом** процессе файл G остаётся и отдаёт исходные байты.

### 6.4 Совместимость `get_rules_snapshot`

`RulesWorkbookSnapshot.local_path` после hit = **`capture_path` текущего поколения**, не обязательно env-путь и не обязательно `_RULES_LOCAL`.

Следствие: повторное чтение `local_path` даёт байты **того** поколения; повторное чтение env-файла — нет.

Callers, которые **открывают** `.local_path` (pandas / xlsx): `config_manager`, `wallet_editor_partner_resolve`, `raccoon_wallet_analyzer`, `raccoon_hourly_report`, `raccoon_wallet_config_loader`, `payout_config_loader`, `ops_rules_validate_summary` — получают стабильный capture. Это **желаемо**.

Callers, которые сравнивают `.local_path` с `RULES_XLSX_PATH` или `_RULES_LOCAL`, **могут разъехаться**. В code TASK-31: не менять их молча, кроме документации/`source` string. Поле `source` / диагностический `source_path` оставить для логов (`job_runner.rules_source`). Тесты `test_get_rules_snapshot_*` сверяют version/meta, не равенство path env.

Miss (нет PublishedState): прежняя логика получения материала, но download только в attempt-capture; канон — только через §6.2.

Не обещать `local_path == canon_path == RULES_XLSX_PATH`.

### 6.5 Audit

Отказы (`reject`) пишем. Payload: `attempt` / `reject` / `discard` / `commit`. I/O audit вне lock.

---

## 7. Locks

| Lock | Держит | Не держит |
|------|--------|-----------|
| Provider | ссылка published; `++latest_attempt` / `++publish_seq`; invalidate; **проверка attempt + `os.replace` канонов** | download, copy тела capture/temp, parse, evaluate, `build_indexes`, audit I/O, Telegram, admission |
| Instance | `_snap`, `_snap_epoch`; вложен только при уже взятом provider lock | provider compute |
| Admission | только `submit_if_open` | всё выше |

Eager indexes до commit, полный объект. Lazy subset-update запрещён.

---

## 8. Закрытые решения

| Тема | Решение |
|------|---------|
| Indexes | Eager |
| Audit | attempt/reject/discard/commit |
| Три конфликта force | `RulesPublishConflictExhausted`, не snapshot, не `stale_reuse` |
| `stale_reuse` | только legacy build/load fail |
| `AccessRules.invalidate` | instance epoch |
| Проигрыш при чужом fresh | `existing` |
| Isolated vs stale | isolated без reset; mixed без молчаливой смены |
| Capture | неизменяемый; **опубликованные** живут до конца процесса; без `G < publish_seq - 1`; без leases; чужие process-dir не чистить; **неопубликованные** — best-effort unlink, одна повторная попытка после `gc.collect()`, затем warning |
| `get_rules_snapshot.local_path` | `capture_path` на hit |
| Replace | в секции commit; ошибки workbook vs identity раздельно |

---

## 9. Mixed и прочие callers

Правка общая. Публичный stale-reuse force **как сейчас** (возврат snapshot). Isolated — §4.1. Пара §2.1 — accessor. Path-callers §2.2 читают capture через `local_path`.

---

## 10. Матрица будущих проверок

`test_repro_*` на `1eefc54` — история. Активные тесты после code — безопасность.

| # | Сценарий | Ожидание |
|---|----------|----------|
| G1 | Старый AccessRules reader после reload | instance = новое; локальный v1 не записан |
| G2 | Старые indexes | только вместе со snapshot того PublishedState |
| G3 | Два force, обратный финиш | новый `publish_seq`; ранний discard |
| G4 | invalidate во время compute | discard; канон не от проигравшего; `publish_seq` жив; capture опубликованных жив |
| G5 | `rejected` | `ContractPublishRejected`; isolated без reset |
| G6 | одно PublishedState | snapshot/decision/indexes/`capture_path` |
| G7 | следующий reader | reuse после freshness + generation + epoch |
| G8 | нет deadlock | Event/barrier |
| G9 | closed/ACL deny | нет force commit |
| G10 | invalidate → публикация | `generation` не повторяется |
| G11 | два старых read API | могут разъехаться; accessor — одно поколение |
| G12 | смена provider во время CAS | `do_not_store` |
| G13 | workbook `replace` fail | память жива; capture жив; канон мог не обновиться |
| G14 | identity `replace` fail / обратный порядок | канон identity только победителя; проверка+replace в одной секции |
| G15 | правка локального xlsx **во время** parse | поколение = capture |
| G16 | legacy `stale_reuse` vs isolated | snapshot без нового `publish_seq`; isolated без reset |
| G17 | старый writer стоп между prepare и секцией commit | новый commit; канон не меняет старый |
| G18 | локальный workbook изменён **без** invalidate | freshness miss; новый compute, не reuse старого поколения |
| G19 | истёк TTL remote | miss; не hit |
| G20 | смена policy | miss |
| G21 | обычный cache hit | нет нового attempt / force |
| G22 | reader старого поколения после нового commit **и** invalidate | читает свой `capture_path`; файл не удалён |
| G23 | `conflict_exhausted` | `RulesPublishConflictExhausted` после 3 попыток; не `stale_reuse`; не успешный snapshot |
| G24 | reader сохраняет `local_path` поколения G; затем ≥3 следующих публикации и invalidate; порядок через Event/barrier, **без** sleep | открытие сохранённого пути отдаёт **исходные** байты G |

---

## 11. Файлы будущего code scope

**Обязательно:**

- `core/rules_provider.py` — `publish_seq`, PublishedState (три пути), freshness, attempt CAS + `os.replace` в одной секции, **process-unique** capture dir (без sweep опубликованных), `get_published_state()`, `RulesPublishConflictExhausted`, `get_rules_snapshot.local_path` = capture на hit, eager indexes, audit kinds
- `core/access_rules.py` — freshness через accessor, epoch, CAS
- `modules/antares/handlers.py` — только isolated `_reload_bound_rules`
- callers пары §2.1 (шесть integrations/analyzers + AccessRules)
- тесты `test_gen_*` включая G17–G24; `tests/rules_v2/test_rules_provider_*.py`, `test_contract_publish_c4.py`; historical repro не safety
- docs: эта страница, карточка TASK-31

**Проверить без молчаливой смены семантики path:** callers §2.2 (`config_manager`, raccoon analyzers, `payout_config_loader`, `partner_resolve`, `ops_rules_validate_summary`, `job_runner` source string). Если тест сравнивает `local_path` с env/`_RULES_LOCAL` — поправить ожидание на capture.

**Не в этом code:** смысл `evaluate_snapshot_publish`, mixed reload, `WorkAdmission`, ingest, schedules loop, Railway, live credentials.

---

## 12. Вне scope

Runtime **этого** docs PR. Merge/retarget/deploy. Закрытие TASK-30 / смена PR #33. Смена C4 policy. Antares-only fork provider. Code — TASK-32.
