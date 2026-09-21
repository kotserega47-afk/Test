# Согласованная публикация rules snapshot и indexes (TASK-31)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **подготовлен к review**, не принят, runtime **не** менялся |
| **База** | TASK-30 HEAD `1eefc54720ccd036451f7c3c0e7dadaedf6efb98` (Draft PR #33, TASK-30 **не** закрыт) |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Repro исходного дефекта** | `test_repro_stale_access_rules_publish_after_reload`, `test_repro_provider_indexes_stat_tears_from_stale_publisher` на том SHA |

Этот документ — **будущий code scope**. Смысл strict/shadow/legacy и stale-reuse **не** менять здесь. Live credentials, merge, deploy, исходное Test — вне scope.

---

## 0. Дефект, который фиксируем (исходники на `1eefc54`)

`core/rules_provider.py`:

- `get_snapshot_v2` пишет `_last_v2_stat`, `_last_v2_snapshot`, `_last_v2_decision` тремя присваиваниями.
- `get_indexes_v2` строит indexes от **локального** `snapshot`, затем `_last_indexes_stat = _last_v2_stat` (уже чужое поколение).
- `get_rules_snapshot` / `_download_rules_workbook_atomic` делает `part.replace(_RULES_LOCAL)` **до** успешной публикации in-memory.
- `_try_save_identity_registry_after_publish` пишет identity registry после `publish_allowed`, без номера поколения.
- `AccessRules.invalidate` **не** вызывает `invalidate_rules_v2_cache`.

`core/access_rules.py` `get_snapshot`: каждый вызов заново зовёт `get_snapshot_v2` + `build_indexes`, затем может заменить `self._snap`. Старый reader, закончивший сборку позже reload, откатывает `_snap`.

`mtime`/`size` файла (`_stat_key`) и `meta.updated_at` **не** номер поколения: два compute могут увидеть один stat; stale writer всё равно может опубликовать позже.

TASK-30 isolated `/reload_rules` **заблокирован** этим дефектом: допуск команды не делает snapshot согласованным.

---

## 1. Единое опубликованное поколение

Один объект (имя code PR; поля обязательны по смыслу):

| Поле | Смысл |
|------|--------|
| `generation` | монотонный `int` процесса, **не** stat файла, **не** timestamp |
| `attempt_id` | монотонный id compute, который **выиграл** commit |
| `snapshot` | `RulesSnapshotV2` |
| `decision` | тот же `SnapshotPublishDecision`, что породил snapshot |
| `workbook` | path + `stat_key` как **атрибут** поколения, не его идентичность |
| `indexes` | `RulesIndexes`, построенные **из этого** snapshot |
| `policy_mode` | `legacy` / `strict` / `shadow` на момент commit |

Публикация — **одно** присваивание ссылки на этот объект (или замена целиком под lock). Запрещено обновлять subset полей у уже видимого поколения.

Чтение: reader копирует ссылку на объект под lock и дальше работает с ней без lock. Поля одного поколения согласованы между собой.

`invalidate_rules_v2_cache` / проигрыш commit / ошибка publish: поколение либо прежнее (ссылка не менялась), либо пустое после явного invalidate — не смесь v1 snapshot + v2 indexes.

Workbook TTL (`_last_rules_wb`, `_last_rules_sync_ts`) — **материал** для compute, не замена `generation`. Смена файла без commit не считается опубликованным поколением.

---

## 2. AccessRules и поколение provider

`AccessRules._snap` — производный ACL-снимок. Он обязан хранить `provider_generation` того PublishedState, из которого собран.

Протокол `get_snapshot(force_sync=)`:

1. Под lock provider: при `force_sync=False` и кэше hit — взять PublishedState.
2. Compute (force или miss) — § 3, вне lock.
3. Собрать производный `Snapshot` **из того же** PublishedState (snapshot + indexes поколения). Не звать второй независимый `get_snapshot_v2`.
4. Под lock AccessRules: записать `self._snap` **только если** `provider_generation` всё ещё равен поколению, из которого сборка. Иначе **не** откатывать `_snap`.

Старый reader **может** вернуть своему вызывающему согласованный старый производный snapshot (локальная переменная). Он **не** имеет права сделать этот объект новым `self._snap` / новым PublishedState, если поколение уже ушло.

`AccessRules.invalidate`: только сброс производного `_snap` (как сейчас: provider cache сам не чистится). Это **не** успешный reload. Открытое решение: должен ли invalidate ещё bump'ать `latest_attempt`, чтобы in-flight производная сборка не записала `_snap` — **да, локально на экземпляре** (`_snap_epoch++`); provider generation не обязан меняться (совместимость с сегодняшним `invalidate` без `invalidate_rules_v2_cache`).

---

## 3. Readers / writers / invalidate

| Роль | Символы сейчас | В протоколе |
|------|----------------|-------------|
| Publish writer | `get_snapshot_v2` (cache miss / `force_sync=True`) | единственный commit PublishedState |
| Index writer | `get_indexes_v2` | **не** отдельный writer: indexes живут на поколении; miss indexes при готовом snapshot — compute indexes **для этой** ссылки, commit только если generation не сменился |
| Invalidate provider | `invalidate_rules_v2_cache`, `config_manager.clear_rules_caches` | bump `latest_attempt` + `invalidate_epoch`; drop PublishedState |
| Invalidate derived | `AccessRules.invalidate` | bump instance epoch; `_snap = None` |
| Readers | `get_snapshot_v2(False)` hit; `get_indexes_v2(False)` hit; `AccessRules.get_snapshot(False)` при совпадении generation; `get_rules_snapshot` (материал); `job_runner.request_job`; `core/schedules.py`; `integrations/telegram_routes.py`; WE settings/auto-enable/registry; analyzers wallet/hourly/conversion; reporters hourly | читают ссылку поколения или материал workbook |
| Reload | `handlers._reload_bound_rules` | `AccessRules.invalidate` → `get_snapshot(force_sync=True)` → при **успешном commit нового поколения** `request_scheduler_clocks_reset` |

ACL `check_access` → `get_snapshot()` без `force_sync`: reader, не reload.

`WorkAdmission` lock **не** участвует в этом протоколе и **не** держится на I/O или compute правил.

---

## 4. Точка успешной публикации и успешный reload

**Успешный commit поколения:** под lock выполнена замена PublishedState на новый объект; `generation` строго больше предыдущего (или первое после пустого кэша).

**Успешный `/reload_rules`:**

1. Допуск (TASK-30) принял работу.
2. `invalidate` производного `_snap`.
3. `get_snapshot(force_sync=True)` **закончился commit'ом** нового поколения (после возможных внутренних discard+retry — открытое решение § 8).
4. Возвращённый `AccessRules` snapshot имеет тот же `provider_generation`.
5. Только тогда `request_scheduler_clocks_reset(reason="reload_rules")`.

Не успех: отказ допуска; исключение snapshot/publish policy; все попытки force discard без нового commit. Тогда **нет** reset и нет ответа «перечитан». Откат `AccessRules.invalidate` **не** обещается (как TASK-30).

---

## 5. Compute вне lock: защита от устаревшей публикации

Одного RLock на присваиваниях **мало**: между download/parse и записью другой поток уже мог опубликовать новее.

Протокол attempt:

```text
under lock:
  my_attempt = ++latest_attempt
  start_epoch = invalidate_epoch
  start_generation = published.generation or 0
unlock
compute (download to staging, evaluate, build indexes, optional audit payload)
under lock:
  if my_attempt != latest_attempt: discard   # более новый compute/invalidate стартовал
  if start_epoch != invalidate_epoch: discard
  if published.generation != start_generation and not force_sync_intent: ...
  # force: публиковать только если мы всё ещё «последний стартовавший»
  commit PublishedState(generation=start_generation+1, attempt_id=my_attempt, ...)
  promote staging workbook / identity only here
unlock
```

`mtime`/`stat` сверяют «тот ли файл мы разбирали», но **не** заменяют `my_attempt`.

### Два одновременных `force_sync=True`

Оба берут разные `attempt_id`. Побеждает **последний стартовавший**, если он дошёл до commit первым или единственным.

Порядок завершения **обратный** старту: ранний compute, финишировавший позже, **discard**. Он не откатывает уже опубликованное новое состояние.

Проигравший force: **не** success reload. Поведение для вызывающего — открытое решение § 8 (один внутренний retry vs ошибка vs wait на победителя и вернуть его snapshot, если generation уже новый и `force_sync` удовлетворён «есть поколение строго новее start»).

Рекомендация контракта (к review, не принято): проигравший `force_sync` **не** публикует; если PublishedState.generation > start_generation, вернуть **уже опубликованное** новое поколение (цель force — свежий snapshot, не обязательно «мой» compute). Если поколение не выросло (оба проиграли из-за invalidate), ошибка или повтор — § 8.

### Invalidate во время compute

`invalidate_rules_v2_cache`: `invalidate_epoch++`, `latest_attempt++`, PublishedState = empty. In-flight commit видит смену epoch/attempt → discard. Disk staging **не** promote. Identity **не** save.

`AccessRules.invalidate` во время производного compute: instance epoch++; сборка может вернуть локальный snap вызывающему, но не пишет `_snap`.

### Ошибка нового reload / publish policy

`ContractPublishRejected` и прочие ошибки compute: **нет** commit, **нет** promote workbook, **нет** identity save, **нет** clocks reset. Действует текущая policy:

- **legacy:** blocking contract → reject; build/load fail при наличии прошлого snapshot → **stale reuse прошлого PublishedState** (не новое поколение, не чужие indexes).
- **strict:** `publish_allowed=False` → reject, кэш не подменяем смесью.
- **shadow:** как сейчас в `evaluate_snapshot_publish` / `get_snapshot_v2` (не менять смысл).

Проигравший/ошибочный compute не должен «починить» кэш частичной записью.

---

## 6. Побочные эффекты на диске (не только globals)

| Эффект | Сейчас | Контракт |
|--------|--------|----------|
| Download | `_download_rules_workbook_atomic`: `.part` → `replace(_RULES_LOCAL)` внутри `get_rules_snapshot`, до C4 commit | Каждому attempt — **свой** staging файл. `replace` в канонический cache path **только на выигравшем commit**. Проигравший удаляет staging. |
| Local `RULES_XLSX_PATH` file | `_try_local_workbook_path` читает чужой файл in place | Не заменять пользовательский workbook. Staging только для cache Dropbox (`_RULES_LOCAL`). |
| Audit | `try_append_publish_audit_trail` внутри `get_snapshot_v2` до/вокруг policy | Не держать publish-lock. Либо буфер и append **после** commit с `attempt_id`, либо append с пометкой attempt и игнор для discard (предпочтение: **после** commit, чтобы устаревший compute не писал «успешный publish»). Открытое решение, если audit сегодня нужен и при reject. |
| Identity | `_try_save_identity_registry_after_publish` при `publish_allowed` | Только победитель commit. `suppress_identity_registry_save` без изменений. Ошибка save по-прежнему ignored, **не** откатывает in-memory поколение. |
| `clear_rules_caches` | чистит pandas-кэши + `invalidate_rules_v2_cache` | Остаётся invalidate provider поколения. |

Устаревший compute **не** имеет права `replace` канонический `rules.xlsx` кэша и **не** имеет права `save_identity_registry` поверх более нового commit.

---

## 7. Вложенные вызовы и lock (без deadlock)

Потоки lock:

| Lock | Держит | Не держит |
|------|--------|-----------|
| Provider publish lock (RLock допустим только если один и тот же поток читает hit внутри `get_indexes_v2` → `get_snapshot_v2`) | чтение ссылки PublishedState; `++latest_attempt`; commit; invalidate | download, zip/xlsx parse, `evaluate_snapshot_publish`, `build_indexes`, audit I/O, identity I/O, Telegram, admission |
| AccessRules instance lock | чтение/запись `_snap` + instance epoch | provider compute, admission |
| Admission lock | только `submit_if_open` | всё выше |

Порядок, если когда-либо оба нужны: **не** брать provider lock, уже держа AccessRules lock, если provider может снова взять AccessRules — сейчас AccessRules вызывает provider, значит: AccessRules lock **после** возврата из provider, либо не держать AccessRules lock на время `get_snapshot_v2`.

Рекомендация: `get_snapshot` не держит instance lock во время provider call. Схема: provider → локальный derived → короткий instance lock для CAS `_snap`.

`get_indexes_v2` → `get_snapshot_v2`: при cache hit — тот же поток, без второго lock (или RLock). При miss — не держать lock на compute.

Не вводить lock `WorkAdmission` в provider.

---

## 8. Open decisions (не «принято»)

1. Проигравший `force_sync`: вернуть чужой более новый PublishedState vs один retry vs ошибка.
2. Писать ли audit при reject (как сейчас) или только после commit.
3. Eager indexes всегда в том же compute, что snapshot, vs lazy indexes на том же объекте поколения (CAS только indexes-поля **запрещён** — только новый объект или одно поле indexes до первой публикации наружу; lazy допустим, если indexes заполняются на копии до публикации ссылки **или** отдельным CAS «indexes is None → set», без смены snapshot).
4. Нужен ли bump `latest_attempt` на `AccessRules.invalidate` в provider (скорее нет; только instance epoch).
5. Сколько retry на discard при непрерывном invalidate (рекомендация: конечное N, затем ошибка как сейчас snapshot fail).

Смысл policy modes — **не** open: не менять.

---

## 9. Влияние на mixed и прочих callers

Правка `rules_provider` + `AccessRules` общая для **mixed / isolated Antares / Raccoon / WR**, кто импортирует эти модули.

| Caller | Файл | Эффект |
|--------|------|--------|
| Isolated `/reload_rules` | `modules/antares/handlers.py` `_reload_bound_rules` | success = commit поколения; иначе без reset |
| Mixed `/reload_rules` | тот же callback, sync | та же семантика commit |
| Jobs | `core/job_runner.py` `get_rules_snapshot` + `get_snapshot_v2(False)` | реже torn fingerprint; чуть сериализованные hit |
| Schedules | `core/schedules.py` | чтение поколения |
| WE | `wallet_editor_*_settings.py` snapshot+indexes подряд | должны видеть одно поколение |
| Analyzers / conversion / hourly reporters | `analyzers/*`, `reporters/hourly_*` | hit согласован; force_sync в analyzer — тот же attempt-протокол |
| `telegram_routes.py` | routing | reader |
| `clear_rules_caches` | `config_manager.py` | invalidate поколения |

Timing: commit сериализован; compute параллелен. Это **меняет** гонки (цель) и может сдвинуть latency force reload при конкуренции. Не меняет CLI/API `force_sync=`.

Не реализовывать Raccoon/WR отдельно в этом code; они получат поведение «бесплатно» через общие модули — это нужно явно принять на review.

---

## 10. Матрица будущих проверок

Существующие `test_repro_*` на TASK-30 **сохранить** как доказательство исходного дефекта (ожидание torn/stale). В code TASK-31 **не** считать зелёный repro исправлением: либо пометить historical, либо заменить **копии** на `test_gen_*` с ожиданием согласованности (repro-файлы не перекрашивать в safety без смены assert).

Будущие проверки (реальный executor/provider cache; workbook/Dropbox/PG/Telegram/identity — sandbox; запись только в tmp):

| # | Сценарий | Ожидание после TASK-31 |
|---|----------|-------------------------|
| G1 | Старый AccessRules reader заканчивается после нового reload | `_snap.generation` = новое; локальный return старого reader может быть v1, но не записан в instance |
| G2 | Старые indexes заканчиваются после смены поколения | PublishedState.indexes от того же snapshot, что decision; нет v1 indexes + v2 stat |
| G3 | Два force reload, обратный порядок завершения | опубликован attempt с большим `attempt_id`; ранний discard; нет отката |
| G4 | `invalidate_rules_v2_cache` во время compute | discard; нет promote файла; нет identity |
| G5 | Ошибка publish / `ContractPublishRejected` | нет commit, нет clocks reset, нет «перечитан»; stale reuse только по **действующей** policy |
| G6 | source/stat/snapshot/decision/indexes | все с одного PublishedState |
| G7 | Следующий reader после успеха | видит новое поколение без `force_sync` |
| G8 | Нет deadlock | barrier/Event как TASK-30; admission lock не на compute |
| G9 | Closed/ACL deny reload | нет force commit (как TASK-30) |

---

## 11. Точные файлы будущего code scope

Менять:

- `core/rules_provider.py` — PublishedState, attempt/epoch, staging download, commit, indexes на поколении
- `core/access_rules.py` — привязка `_snap` к `generation`, CAS, не откат
- `tests/unit/test_antares_work_admission.py` — `test_gen_*`; repro оставить
- `tests/rules_v2/test_rules_provider_*.py` / `test_contract_publish_c4.py` — cache hit/invalidate без смены policy
- docs: эта страница, карточка TASK-31, ссылка с TASK-30

Не менять в этом code (если не вылезет вынужденно из PublishedState): `evaluate_snapshot_publish` смысл, `handlers` admission, `WorkAdmission`, mixed gate, ingest, schedules loop, Railway.

Возможный узкий хвост: `handlers._reload_bound_rules` только если нужно явно отличить «вернулся snapshot, но commit не наш» — предпочтительно спрятать в `get_snapshot(force_sync=True)`.

---

## 12. Вне scope

Production runtime в **этом** docs PR. Merge/retarget/deploy. Закрытие TASK-30. Смена base PR #33. Исправление policy semantics. Отдельный Antares-only fork provider (запрещён: сломает смысл общей правки). Live Dropbox credentials.
