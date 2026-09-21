# Согласованная публикация rules snapshot и indexes (TASK-31)

| Мета | Значение |
|------|----------|
| **Статус** | контракт **подготовлен к review**, не принят, runtime **не** менялся |
| **База** | TASK-30 HEAD `1eefc54720ccd036451f7c3c0e7dadaedf6efb98` (Draft PR #33, TASK-30 **не** закрыт) |
| **Admission** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Repro исходного дефекта** | `test_repro_*` на SHA `1eefc54` (история дефекта, не будущий safety-критерий) |

Этот документ — **будущий code scope**. Смысл strict/shadow/legacy и provider stale-reuse **не** менять. Live credentials, merge, deploy, исходное Test, закрытие TASK-30 — вне scope.

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
| `workbook` | path + `stat_key` **атрибут** поколения, не его идентичность |
| `indexes` | `RulesIndexes`, построенные **в том же compute** из этого snapshot (eager, без lazy subset-update) |
| `policy_mode` | `legacy` / `strict` / `shadow` на момент commit |

Публикация — **одно** присваивание ссылки на новый объект. Запрещено менять поля уже видимого поколения.

### 1.1 Счётчик `publish_seq` (анти-ABA)

Отдельный process-local счётчик успешных публикаций:

- стартует с 0 в процессе;
- **никогда не сбрасывается** при `invalidate_rules_v2_cache` / пустом PublishedState;
- каждая **успешная** публикация: `publish_seq += 1`, новый объект несёт это значение как `generation`.

Invalidate **очищает ссылку** PublishedState (`None` / empty). Следующий commit получает `generation = 2, 3, …`, **не** повторный `1`.

Неверно: `start_generation = 0` при empty, затем `generation = start_generation+1` → после invalidate снова `1` (ABA: старый reader с `generation==1` может принять чужое новое состояние за «то же»).

`latest_attempt` и `invalidate_epoch` — отдельные счётчики стартов compute / invalidate; они тоже не обнуляются. `mtime`/`stat` их не заменяют.

---

## 2. Согласованный read API

**Не обещаем**, что два независимых вызова `get_snapshot_v2()` и `get_indexes_v2()` относятся к одному поколению. Каждый вызов сам по себе читает актуальное PublishedState в свой момент; между ними возможен чужой commit.

Единый accessor (имя code PR, например `get_published_state()`):

- под lock копирует **ссылку** на PublishedState (или инициирует compute по §5);
- возвращает один объект: snapshot + decision + workbook + indexes + `generation`.

Существующие `get_snapshot_v2` / `get_indexes_v2` / `get_rules_snapshot` остаются. Их контракт после исправления: каждый возвращает поле **какого-то** поколения на момент вызова, без пары. Callers, которым нужна согласованная пара, **обязаны** перейти на единый accessor.

### 2.1 Callers, которым нужна пара (входят в будущий code scope)

Сейчас два вызова подряд:

| Файл | Вызовы |
|------|--------|
| `integrations/wallet_editor_registry_settings.py` | `get_snapshot_v2` + `get_indexes_v2` |
| `integrations/wallet_editor_registry_refresh.py` | то же |
| `integrations/wallet_editor_auto_enable_settings.py` | то же |
| `integrations/wallet_editor_auto_enable.py` | то же |
| `analyzers/wallet_analyzer.py` | то же (несколько мест) |
| `analyzers/hourly_analyzer.py` | то же |
| `core/access_rules.py` | `get_snapshot_v2` + свой `build_indexes` — перейти на PublishedState.indexes, не второй независимый load |

### 2.2 Callers одного поля (не обещать пару)

| Файл | Что берут |
|------|-----------|
| `core/schedules.py`, `integrations/telegram_routes.py`, `integrations/conversion_fingerprint.py`, `analyzers/conversion.py`, `reporters/hourly_reporter.py`, `reporters/hourly_render_model.py` | snapshot |
| `core/job_runner.py` | `get_rules_snapshot` затем отдельно `get_snapshot_v2` (fingerprint) — **два поколения возможны**; если нужен один — accessor; иначе оставить как есть с явной пометкой «не пара» |
| `core/config_manager.py`, `integrations/wallet_editor_partner_resolve.py`, `analyzers/raccoon_*`, `analyzers/payout_config_loader.py`, `core/rules_v2/ops_rules_validate_summary.py` | workbook path / `get_rules_snapshot` |

`get_indexes_v2` после исправления: `return get_published_state(...).indexes` (один snapshot на этот вызов, не склеенный с предыдущим `get_snapshot_v2` у caller).

---

## 3. AccessRules: generation + instance epoch, без окна

Держать **только** instance lock недостаточно: provider может сменить PublishedState между проверкой и записью `_snap`.

Порядок блокировок **всегда** `provider lock → instance lock`. Никогда наоборот. Provider lock не держат на I/O/compute. Admission lock не участвует.

`AccessRules` поля: `_snap`, `_snap_epoch` (instance). `invalidate()`: под instance lock `_snap_epoch += 1`, `_snap = None`. Provider `publish_seq` не трогает.

### 3.1 Начало вызова (следующий reader)

Под **provider затем instance**:

- если `_snap is not None` и `_snap.provider_generation == published.generation` и epoch совпал с текущим `_snap_epoch` — **reuse** `_snap`;
- иначе reuse запрещён (поколение ушло или invalidate).

Пустой published / miss / `force_sync=True` → compute §5, без instance lock на время compute.

### 3.2 CAS записи `_snap` после сборки

Зафиксировать в начале вызова `start_epoch = _snap_epoch` (короткий instance lock или вместе с 3.1). Собрать производный `D` из PublishedState `P` (локально). Затем:

```text
lock provider
  current = published          # ссылка
  lock instance
    if _snap_epoch != start_epoch:
        do_not_store           # invalidate во время сборки
    elif current is None or current.generation != P.generation:
        do_not_store           # provider ушёл вперёд
    else:
        _snap = D              # D.provider_generation == P.generation
  unlock instance
unlock provider
return D                       # вызывающему этой инвокации всегда согласованный D из P
```

Старый reader **возвращает** свой `D` (v1), даже если `do_not_store`. Он **не** записывает v1 поверх нового `_snap`.

Окно «проверил generation без provider lock, потом записал» **запрещено**.

---

## 4. Provider outcome vs команда reload

Публичный `get_snapshot_v2` / `get_indexes_v2` / `get_rules_snapshot` сохраняют типы и **legacy force / stale reuse** как сейчас: нет исключения → возвращается snapshot (в т.ч. stale). Смысл policy не менять.

Внутренний результат compute (метаданные, не обязательно новый публичный enum снаружи модуля):

| Код | Когда | PublishedState | Поколение |
|-----|--------|----------------|-----------|
| `fresh_commit` | этот attempt выиграл CAS и опубликовал | новый объект | `publish_seq` вырос |
| `existing` | cache hit **или** проигрыш attempt, но уже есть поколение **новее** `observed_generation` на старте | без изменения этим attempt | не выросло этим attempt |
| `stale_reuse` | legacy: build/load fail, отдан **прошлый** PublishedState | тот же объект | **не** выросло |
| `rejected` | `ContractPublishRejected` / policy запретила publish | без commit | не выросло |
| `conflict_exhausted` | проигрыш attempt и более поздний attempt тоже не опубликовал (упал / discard / invalidate), исчерпаны попытки | как до вызова | не выросло |

`existing` при проигрыше force, если чужой attempt уже сделал `fresh_commit`, — это **не** stale_reuse.

### 4.1 Команда `/reload_rules`

Смешанный (mixed) callback **не** менять молча: как сейчас — нет исключения из `get_snapshot(force_sync=True)` ⇒ reset + «перечитан», в том числе на **stale_reuse**. Исключение ⇒ warning, без reset.

Isolated (и только он) отличает исходы через метаданные `get_snapshot` / узкий helper рядом с `_reload_bound_rules` (не ломая публичный provider):

| Исход provider | Isolated reset | Isolated ответ |
|----------------|----------------|----------------|
| `fresh_commit` | да | «перечитан», source нового поколения |
| `existing` (поколение > чем после invalidate / старта команды) | да | «перечитан», source **того** поколения (чужой выигравший force) |
| `stale_reuse` | **нет** | warning: не новый commit, правила не сброшены как reload |
| `rejected` / `conflict_exhausted` | нет | warning как ошибка перечитывания |

Mixed **не** получает эту развилку в TASK-31 code, пока отдельно не решат выровнять. Не обещать, что isolated и mixed одинаково трактуют stale.

Откат `AccessRules.invalidate` при ошибке **не** обещается.

---

## 5. Compute вне lock (attempt)

```text
observed_generation = published.generation if published else None
# None — пусто, это НЕ «0 для следующего generation»

under lock:
  my_attempt = ++latest_attempt
  start_epoch = invalidate_epoch
unlock

compute:
  capture workbook (§6)
  evaluate + eager build_indexes
  buffer audit payload (attempt id)

under lock:
  if invalidate_epoch != start_epoch: discard
  if my_attempt != latest_attempt: discard
  else:
    publish_seq += 1
    published = PublishedState(generation=publish_seq, attempt_id=my_attempt, ...)
    # память — source of truth in-process
unlock

if discarded: audit discard; cleanup staging; see §4 for caller
if committed: short file protocol §6; audit commit
```

`generation` нового объекта **всегда** `publish_seq` после инкремента, никогда `observed_generation+1` с подстановкой 0.

Проигравший attempt **не** публикует и **не** откатывает `published`.

### 5.1 Два force, обратный финиш

Побеждает последний **стартовавший**, который прошёл CAS (`my_attempt == latest_attempt`). Ранний финишировавший позже — discard.

### 5.2 Проигрыш force, когда поздний attempt тоже упал

Нет `existing` с выросшим поколением. Caller получает `conflict_exhausted` после **конечных** повторов (§5.4), не бесконечный цикл. Состояние — прежний PublishedState (если invalidate его не снёс). Isolated: без reset. Публичный provider, если вызывали `get_snapshot_v2(force_sync=True)`: сохранить текущую semantics ошибки (исключение, если нечего вернуть) или прежний snapshot только там, где policy и сегодня так делает — **не** выдавать stale_reuse за fresh.

### 5.3 Invalidate во время compute

`invalidate_rules_v2_cache`: `invalidate_epoch++`, `latest_attempt++`, `published = None`. `publish_seq` **не** трогать. Staging не promote, identity не save, audit **discard**.

### 5.4 Конечные конфликты

Цикл force: не более **3** полных attempt на один публичный вызов `force_sync=True` (включая первый). Policy `rejected` **не** ретраить. После 3 discard/conflict → `conflict_exhausted` / ошибка как сбой snapshot. Не `while True`.

---

## 6. Один протокол памяти и файлов

**Не обещаем** атомарность in-memory поколения и нескольких файлов. Источник истины **в процессе** — ссылка PublishedState. Диск — best-effort с токеном `(generation, attempt_id)`.

Обычные ошибки (`OSError` replace/save) ≠ аварийный kill процесса. Kill: следующее поднятие процесса заново читает канонические файлы; in-memory поколения нет.

### 6.1 Порядок (выбран один)

1. **Compute:** материал только в **staging/capture**, канон `_RULES_LOCAL` и identity canonical **не** трогать.
2. **CAS + память** под lock (§5).
3. **После unlock, токен commit'а:**
   - workbook: `replace` канона из staging **только если** повторная короткая проверка `published.attempt_id == my_attempt`; иначе удалить staging.
   - identity: писать во **temp с generation в имени/заголовке**; короткая проверка того же attempt; затем replace канона. Если attempt уже не победитель — temp удалить, канон не трогать.
4. Ошибка `replace` workbook: память **не** откатывать; `workbook.path` поколения остаётся на **живом staging**; залогировать; promote можно не ретраить в этом вызове. Reader этого поколения читает staging path из PublishedState, не обязательно канон.
5. Ошибка identity save: как сейчас ignored для in-memory; канон identity не частично писать (только complete temp+replace).

Обратный порядок identity: старый победитель не имеет права replace канона, если `published.attempt_id` уже другой — проверка **сразу перед** replace, не только перед началом I/O.

### 6.2 Локальный `RULES_XLSX_PATH`

Пользовательский файл **не** заменяем. На старте attempt: **копия байт** (или copy в staging). Parse только копии. Правка файла пользователем во время parse не входит в это поколение.

Публичный `get_rules_snapshot`:

- hit PublishedState: вернуть workbook **этого** поколения (`stat_key` с capture; `local_path` для env-файла может остаться путём пользователя ради совместимости — тогда в контракте явно: повторное чтение path **не** гарантирует то же поколение);
- Dropbox cache: `local_path` = канон **или** staging, что записано в PublishedState после шага 6.1;
- miss: прежняя логика TTL/download, но download только в attempt-staging, promote по §6.1.

Не обещать, что `get_rules_snapshot().local_path` всегда канон `_RULES_LOCAL`.

### 6.3 Audit

Сохранить запись **отказов** (reject), как сейчас по смыслу. Различать в payload: `attempt` / `reject` / `discard` / `commit`. Discard и commit не путать с reject. I/O audit вне publish-lock; discard не помечается как commit.

---

## 7. Locks (без deadlock)

| Lock | Держит | Не держит |
|------|--------|-----------|
| Provider | чтение/замена ссылки published; `++latest_attempt` / `++publish_seq`; invalidate; короткая проверка перед file replace | download, parse, evaluate, `build_indexes`, audit I/O, identity body write, Telegram, admission |
| Instance AccessRules | `_snap`, `_snap_epoch`; вложен **только** когда provider lock уже взят | provider compute |
| Admission | только `submit_if_open` | всё выше |

`get_indexes_v2` не берёт второй lock на hit: один published pointer.

Eager indexes: `build_indexes` в compute **до** commit, в объекте сразу полный набор. Lazy subset-update **запрещён** в первом исправлении.

---

## 8. Закрытые решения (больше не open)

| Тема | Решение |
|------|---------|
| Indexes | Eager, неизменяемый PublishedState |
| Audit | Отказы пишем; различаем attempt/reject/discard/commit |
| Проигрыш + падение позднего attempt | `conflict_exhausted`, без commit и isolated reset |
| Постоянные конфликты | ≤ 3 attempt на force-вызов, затем ошибка |
| `AccessRules.invalidate` | только instance epoch; не `publish_seq` |
| Проигравший force при чужом `fresh_commit` | provider `existing`; isolated reset да; mixed без изменений API |
| Isolated vs stale_reuse | isolated **не** считает успехом reload; mixed **сохраняет** нынешний успех без исключения |

---

## 9. Влияние на mixed и прочих callers

Правка общая для mixed / isolated / Raccoon / WR через `rules_provider` + `AccessRules`.

Публичный force/stale **как сейчас**. Isolated команда — отдельная метаданных-развилка (§4.1). Callers из §2.1 **меняются** на единый accessor (иначе пара не обещана).

---

## 10. Матрица будущих проверок

`test_repro_*` на `1eefc54` — **историческое** доказательство дефекта. После code TASK-31 активный набор — `test_gen_*` / обновлённые provider-тесты: **безопасность**, не assert torn/stale как «правильно». Repro не перекрашивать в safety, не оставлять их единственным зелёным критерием.

Запись только sandbox/tmp. Нет live Dropbox/PG/Telegram.

| # | Сценарий | Ожидание |
|---|----------|----------|
| G1 | Старый AccessRules reader после нового reload | instance `_snap` = новое поколение; локальный return старого может быть v1 и не записан |
| G2 | Старые indexes после смены поколения | indexes только вместе с snapshot того PublishedState |
| G3 | Два force, обратный финиш | больший `attempt_id` / новый `publish_seq`; ранний discard |
| G4 | invalidate во время compute | discard; нет promote; нет identity; `publish_seq` не сброшен |
| G5 | `rejected` | нет commit, isolated без reset |
| G6 | одно PublishedState | source/stat/snapshot/decision/indexes |
| G7 | следующий reader | reuse только при том же `generation` |
| G8 | нет deadlock | Event/barrier; admission не на compute |
| G9 | closed/ACL deny | нет force commit |
| G10 | invalidate → новая публикация | `generation` **не** повторяет прежнее значение (не ABA `1`) |
| G11 | два последовательных read API | пара старых вызовов может разъехаться; accessor — одно поколение |
| G12 | смена provider во время CAS AccessRules | `do_not_store`; следующий reader не reuse устаревший `_snap` |
| G13 | `replace` workbook fail | память жива; staging path; канон мог остаться старым |
| G14 | обратный порядок identity save | канон identity от победившего attempt |
| G15 | правка локального xlsx во время parse | поколение = capture, не live file |
| G16 | legacy `stale_reuse` vs isolated reload | provider отдаёт старое без нового `publish_seq`; isolated без reset; mixed без молчаливой смены |

---

## 11. Файлы будущего code scope

**Обязательно:**

- `core/rules_provider.py` — `publish_seq`, PublishedState, attempt CAS, staging, `get_published_state()`, eager indexes, audit kinds, `get_rules_snapshot` совместимость
- `core/access_rules.py` — epoch, порядок lock, CAS §3
- `modules/antares/handlers.py` — **только** isolated ветка `_reload_bound_rules` / чтение метаданных исхода; mixed callback без молчаливой смены
- callers пары §2.1:  
  `integrations/wallet_editor_registry_settings.py`  
  `integrations/wallet_editor_registry_refresh.py`  
  `integrations/wallet_editor_auto_enable_settings.py`  
  `integrations/wallet_editor_auto_enable.py`  
  `analyzers/wallet_analyzer.py`  
  `analyzers/hourly_analyzer.py`
- тесты: новые `test_gen_*` в `tests/unit/test_antares_work_admission.py` и `tests/rules_v2/test_rules_provider_*.py` / `test_contract_publish_c4.py`; historical repro не как safety
- docs: эта страница, карточка TASK-31

**Не в этом code:** смысл `evaluate_snapshot_publish`, mixed reload semantics, `WorkAdmission`, ingest, schedules loop, Railway, live credentials.

`core/job_runner.py` — только если решим, что fingerprint обязан быть той же парой; иначе пометка «не пара» без обязательного изменения.

---

## 12. Вне scope

Runtime **этого** docs PR. Merge/retarget/deploy. Закрытие TASK-30 / смена PR #33. Смена C4 policy. Antares-only fork provider.
