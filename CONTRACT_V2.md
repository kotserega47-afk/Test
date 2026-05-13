# CONTRACT V2 — Design Specification (rules.xlsx + runtime)

**Status:** design spec (normative target; implementation may lag).  
**Scope:** workbook `rules.xlsx` (Control Plane), normalization, validation policy, precedence, runtime invariants, migration, tests.  
**Audience:** operators, reviewers, implementers (bridge, validators, accessors, analyzers, reporters).

---

## 1. Purpose

### 1.1 Зачем нужен контракт

Контракт описывает **единственный воспроизводимый способ** задать бизнес-логику (лимиты, пороги, доступ, расписания, отчёты) через Excel без «магии» в коде. Код остаётся **исполнителем**; источник истины — файл правил + этот документ.

### 1.2 Что он защищает

- **Воспроизводимость:** один и тот же файл + одна версия контракта → одинаковое поведение job и отчётов.
- **Изоляция Control Plane:** изменения поведения без неожиданных правок кода.
- **Наблюдаемость:** версия контракта и правил фиксируется в событиях/логах.
- **Мульти-анализаторы:** явные ключи `job` / `analyzer` / `analyzers` и согласованные scope.

### 1.3 Какие silent failures должен устранить

| Silent failure | Почему опасно | Как V2 это закрывает |
|----------------|---------------|----------------------|
| Дубликат ключа в индексе → «последняя строка победила» | Изменение порядка строк меняет прод без ошибке | MUST FAIL duplicate detection (§7, §9) |
| Расхождение канонизации партнёра (имя в файле vs payout) | Лимиты/пороги не матчатся без exception | Единый `partner_key` contract (§5) + тесты (§15) |
| Скрытый fallback method (`None` после specific) | Ожидали лимит по методу — применился generic | Явная норма + опционально WARN (§8, §7) |
| Пустой / неверный `report` ключ → нули в отчёте | Выглядит как «нет данных» | Validation + golden tests (§7, §15) |
| Пересекающиеся exclusions → победитель по порядку строк | Недетерминизм | Политика overlap (§8) + MUST FAIL или priority |
| Битое расписание отфильтровано без ошибки | Job не запускается незаметно | Schedule MUST FAIL в strict (§7, §9, §14) |
| Дубликат access/commands | Внезапная смена прав Telegram | MUST FAIL (§7) |

---

## 2. Versioning

### 2.1 `meta.version` в Excel

- Лист **`meta`**: строки `key` / `value`.
- Ключ **`version`** — **целое число: версия контракта workbook** (схема листов, колонок, семантики валидации), не версия приложения.
- Потребители (валидатор, CI, опционально runtime) сравнивают `meta.version` с **поддерживаемым диапазоном** для данной ветки кода.

### 2.2 `RulesSnapshotV2` в Python

- **`RulesSnapshotV2`** — **внутренняя нормализованная модель** после загрузки workbook (bridge/loader): dataclass-снимок + индексы + accessors.
- Имя `V2` относится к **архитектуре snapshot в коде**, а не автоматически к значению `meta.version`.

### 2.3 Почему это разные версии

| Артефакт | Что версионирует |
|----------|------------------|
| `meta.version` | Версия **Excel-контракта** (какие листы/колонки/правила валидации актуальны). |
| `RulesSnapshotV2` / `meta.ruleset_version` в snapshot | В runtime в bridge сейчас **`ruleset_version` часто копируется из `meta.version`**, но семантически это «метка набора правил»; в целевом V2 рекомендуется явно различать: `contract_version` (Excel) vs `ruleset_id` / hash (опционально). |

### 2.4 Как они связаны (целевое)

1. Загрузчик читает workbook с **`meta.version = N`**.
2. Если код поддерживает только `N` (strict) — при несовпадении **MUST FAIL** до job.
3. После успешной валидации строится **`RulesSnapshotV2`**; в `MetaInfo.ruleset_version` записывается строка, **согласованная с политикой релиза** (минимум — отражение `meta.version`; максимум — `N` + build stamp отдельным полем в будущем).

**Правило для операторов:** при bump схемы Excel → увеличить **`meta.version`** и обновить этот документ + валидатор.

**Политика смены версий целиком:** §12 (Version migration policy).  
**Совместимость runtime ↔ `meta.version`:** §19.

---

## 3. Excel workbook schema

Общие правила для всех листов:

- **Имена колонок:** после чтения применяется **trim** пробелов по краям заголовка (совместимость с лишними пробелами в `job_params`).
- **Пустые строки:** строка считается **пустой**, если все **обязательные** для типа строки поля пусты; пустые строки **игнорируются** при разборе (WARN если на листе >N подряд пустых — опционально).
- **`enabled`:** если колонка отсутствует — трактовать как **1** только там, где это явно оговорено для обратной совместимости; иначе колонка **обязательна** для V2 strict.
- **Deprecated / ignored колонки:** любая колонка, не перечисленная в «обязательные / опциональные», в **strict** → MUST FAIL; в **legacy** → WARN и колонка игнорируется при сборке snapshot.

Ниже — **целевая** схема V2 (имена листов фиксированы).

### 3.1 `meta`

| | |
|--|--|
| **Назначение** | Идентификация набора правил и **версия контракта** workbook. |
| **Обязательные колонки** | `key`, `value` (ключ-значение). Обязательные **ключи** в данных: `version`; рекомендуется `updated_at`. |
| **Опциональные ключи** | `ruleset_name`, `updated_by`, `comment`. |
| **Deprecated / ignored** | Любые другие ключи — WARN (или MUST FAIL в strict). |
| **Пустые строки** | Игнорировать полностью пустые строки. |
| **`enabled`** | Не применяется (нет колонки enabled на листе). |

### 3.2 `exclude_time`

| | |
|--|--|
| **Назначение** | Временные исключения для указанных анализаторов и партнёра. |
| **Обязательные колонки** | `id`, `enabled`, `analyzers`, `partner`, `start_dt`, `end_dt`, `reason` |
| **Опциональные** | `created_by`, `created_at` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Если нет `partner` или нет интервала — строка invalid → MUST FAIL. |
| **`enabled`** | `0` — строка не участвует. |

**Семантика V2:** одно правило применяется к job, если `job_key` (из CSV `analyzers`) совпадает; scope в snapshot — **partner** (см. §8 exclusions).

### 3.3 `thresholds_partner`

| | |
|--|--|
| **Назначение** | Пороги по метрикам для пары (analyzer/job, partner). |
| **Обязательные колонки** | `id`, `enabled`, `analyzer`, `partner`, `metric`, `reason` |
| **Опциональные** | `threshold_min`, `threshold_max`, `min_events`, `updated_by`, `updated_at` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Игнорировать только если полностью пустая; иначе MUST FAIL при отсутствии обязательных. |
| **`enabled`** | `0` — не индексировать. |

**Уникальность V2 (индекс):** `(normalize_key(analyzer), partner_key, metric_key)` — см. §5; дубликат при двух enabled → MUST FAIL.

**XOR:** для метрик, где требуется ровно одна граница — правило из §7 (наследие v3: XOR для части метрик).

### 3.4 `wallet_limits`

| | |
|--|--|
| **Назначение** | Лимиты (например `daily_max_amount`) в разрезе scope и опционально method. |
| **Обязательные колонки** | `id`, `enabled`, `analyzers`, `scope`, `scope_value`, `limit_type`, `limit_value`, `reason` |
| **Опциональные** | `comment`, `method`, `updated_by`, `updated_at` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Без `limit_value` — MUST FAIL (или skip с WARN только в legacy). |
| **`enabled`** | `0` — не индексировать. |

**Допустимые `scope` (V2):** `partner`, `group`, `global` (если `global` вводится — MUST документирован; иначе MUST FAIL неизвестный scope).

**Уникальность V2:** `(job_key из analyzers, scope_type, scope_key, metric_key, method_key|null)` — дубликат enabled → MUST FAIL.

### 3.5 `access`

| | |
|--|--|
| **Назначение** | Уровень доступа пользователя в чате (Telegram). |
| **Обязательные колонки** | `chat_id`, `user_id`, `level`, `enabled` |
| **Опциональные** | `note` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Игнорировать если нет `user_id` и `chat_id`; иначе MUST FAIL. |
| **`enabled`** | `0` — не индексировать. |

**Уникальность V2:** `(chat_id, user_id)` после нормализации chat — дубликат enabled → MUST FAIL.

### 3.6 `commands`

| | |
|--|--|
| **Назначение** | Разрешённые команды бота и минимальный уровень. |
| **Обязательные колонки** | `command`, `required_level`, `allow_private`, `allow_groups`, `enabled` |
| **Опциональные** | `note` |
| **Deprecated / ignored** | — |
| **Пустые строки** | MUST FAIL если нет `command`. |
| **`enabled`** | `0` — команда не регистрируется. |

**Уникальность V2:** нормализованный текст команды (см. §5) — одна enabled-команда на ключ.

### 3.7 `schedules`

| | |
|--|--|
| **Назначение** | Расписание запуска job по типу. |
| **Обязательные колонки** | `id`, `enabled`, `job_type`, `schedule_type`, `every_seconds`, `cron`, `jitter_sec`, `max_runtime_sec`, `coalesce` |
| **Опциональные** | — |
| **Deprecated / ignored** | Любые столбцы вида `Unnamed:*`, дубли `cron.1` и т.п. — **WARN** в legacy, **MUST FAIL** в strict. |
| **Пустые строки** | Игнорировать полностью пустые. |
| **`enabled`** | `0` — не попадает в runtime список. |

**Валидность строки:** `schedule_type` ∈ {`interval`, `cron`}; для `interval` — `every_seconds` > 0; для `cron` — непустой `cron` — иначе MUST FAIL (strict) или skip с WARN (legacy).

### 3.8 `job_params`

| | |
|--|--|
| **Назначение** | Параметры job в разрезе scope (global / group / partner). |
| **Обязательные колонки** | `id`, `enabled`, `job`, `scope`, `scope_value`, `key`, `value_type`, `value` |
| **Опциональные** | `comment`, `updated_at`, `updated_by` |
| **Deprecated / ignored** | Колонки с лишними пробелами в имени — **нормализуются trim**; в strict лишние неизвестные колонки → MUST FAIL. |
| **Пустые строки** | Игнорировать если нет `job` и `key`. |
| **`enabled`** | `0` — не в индекс. |

**Уникальность V2:** `(job_key, scope_type, scope_key, param_key)` — дубликат enabled → MUST FAIL.

**Каталог ключей `job_params` для `job = hourly` (расширяется синхронно с реализацией и этим документом):**

| `key` | `value_type` | Default при отсутствии строки / нет активного override | Назначение |
|-------|----------------|----------------------------------------------------------|------------|
| `hide_inactive_rows` | `bool` | `false` | Только **presentation** hourly-отчёта (см. §3.8.1). |

**Scope и приоритет:** те же, что для любой строки `job_params` (колонки `job`, `scope`, `scope_value`); разрешение значения — как в runtime (`get_job_params` / snapshot), без отдельной ветки для hourly.

#### 3.8.1 `hide_inactive_rows` — семантика (hourly text only)

Если после разрешения `job_params` для `job = hourly` флаг **`hide_inactive_rows = true`**, текстовый hourly-рендерер **скрывает неактивные строки постфактум** (после построения DTO), сохраняя порядок конфигурации (`sort_order` / ключи layout):

- **Payin:** строка конфигурации (`hourly_payins` → payin row) **не выводится**, если за выбранный период **нет** соответствующего payin-fact (по `source_key`) в DTO.
- **Payout:** строка метода (`hourly_payout_methods`) **не выводится**, если за период **нет** fact для пары `(group_code, method_key)`.
- **Критерий активности (текущий runtime):** `amount != 0` после слияния фактов; **на будущее:** `count > 0 OR amount != 0`, если в DTO появится поле `count`; комментарий при нулевой сумме **не** считается активностью.
- **Заголовок payout-группы** (`hourly_payouts`): **не выводится**, если после фильтрации **не осталось** ни одной видимой строки метода в этой группе.

**Явные non-effects (MUST NOT для этого флага):**

- не меняет агрегацию hourly-analyzer и состав `HourlyDTO`;
- не меняет `rules_v2` provider / accessors / indexes / семантику валидации snapshot (ключ параметра задаётся только через каталог `job_params` для `hourly`, согласованный с реализацией);
- **не** удаляет и **не** игнорирует строки в `rules.xlsx` — влияние только на **финальный текст** hourly-отчёта;
- не влияет на wallet report и прочие view.

#### 3.8.2 Вертикальный отступ перед секциями payouts / payins (hourly text only)

При сборке **текста** hourly-отчёта (layout-renderer для `view = hourly`) реализация **вставляет ровно одну пустую строку** перед **первой** реально выводимой строкой секции **payouts** (ключи layout `payouts.title` / `payouts.items`) и аналогично перед секцией **payins** (`payins.title` / `payins.items`), **если** поток вывода ещё не заканчивается пустой строкой (без двойных blank подряд).

- **Порядок элементов `ui_layout` / `report_items` не меняется** — отступ только в потоке выходного текста (сопоставление ключей — §8.7).

### 3.9 `ui_layout`

| | |
|--|--|
| **Назначение** | Макет отчёта: секции и строки для view (например hourly, wallet UI). |
| **Обязательные колонки** | `id`, `enabled`, `view`, `section`, `order`, `key` |
| **Опциональные** | `title`, `style`, `notes` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Без `view` или без секции/`key` — skip с WARN или MUST FAIL (strict). |
| **`enabled`** | `0` — не попадает в report items. |

### 3.10 `hourly_payins`

| | |
|--|--|
| **Назначение** | Конфигурация групп поступлений (payin) для hourly report. |
| **Обязательные колонки** | `display_name` **или** осмысленная связка для `source_key` (см. bridge): как минимум одно из `group_code`, `display_name`, `source_partners`/`comment` по политике продукта. |
| **Опциональные** | `group_code`, `source_partners`, `comment`, `enabled`, `sort_order`, `group_break_after` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Полностью пустые — **игнор** (WARN при большом количестве). Частично заполненные — MUST FAIL в strict. |
| **`enabled`** | Отсутствие — трактовать как enabled=1 **только** в legacy; V2 strict — колонка обязательна. |

### 3.11 `hourly_payout_methods`

| | |
|--|--|
| **Назначение** | Методы выплат внутри payout group. |
| **Обязательные колонки** | `group_code`, `enabled`, `sort_order` + способ идентификации метода (`method_code` или согласованный fallback в bridge). |
| **Опциональные** | `method_name`, `source_partners`, `comment` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Без `group_code` — skip/MUST FAIL по политике. |
| **`enabled`** | `0` — не включать в report items. |

### 3.12 `hourly_payouts`

| | |
|--|--|
| **Назначение** | Группы выплат для hourly. |
| **Обязательные колонки** | `group_code`, `enabled`, `sort_order` |
| **Опциональные** | `display_name`, `group_break_after` |
| **Deprecated / ignored** | — |
| **Пустые строки** | Без `group_code` — MUST FAIL или skip. |
| **`enabled`** | `0` — не включать. |

### 3.13 `partner_groups`

| | |
|--|--|
| **Назначение** | Принадлежность партнёра к группе по job; default method на уровне membership. |
| **Обязательные колонки** | `id`, `enabled`, `analyzers`, `group_name`, `partner` |
| **Опциональные** | `default_method`, `updated_at`, `comment`, `group_priority`, `is_primary` (§8.3) |
| **Deprecated / ignored** | — |
| **Пустые строки** | Без `group_name` или `partner` — skip/MUST FAIL. |
| **`enabled`** | `0` — membership не активен. |

**V2 расширение (рекомендуется в данных):** колонки `group_priority` / `is_primary` (или одно из) — для детерминированного primary group (§8). Пока колонок нет или они пустые — **legacy** порядок строк + WARN (см. §13).

**Инвариант legacy (runtime):** *If no explicit primary metadata is provided, membership ordering must remain identical to snapshot insertion order* — порядок элементов, который видят `get_group_memberships` и «primary = первый элемент», совпадает с порядком списка `partner_group_members` в `RulesSnapshotV2` после bridge (в prod сегодня это порядок строк Excel). Автоматический stable sort без явных колонок **не допускается** — иначе меняется primary group без явного признака в данных. Регрессии: ``tests/rules_v2/test_membership_order_legacy.py``.

## 4. Entity identity contract

Этот раздел фиксирует, **что является идентичностью сущности** в контракте и что может меняться без смены «той же» строки правила.

### 4.1 Что такое `id`

- **`id`** — **стабильный человеко-читаемый идентификатор строки** в Excel (например `LIM-00001`, `THR-00003`, `JP-00001`).
- Назначение: аудит, ссылки в changelog, сообщения валидатора, поддержка.
- **`id` не заменяет нормализованный бизнес-ключ индекса** для limits/thresholds/params: уникальность в runtime-индексе задаётся ключами из §3 и §5 (например `(job, scope, scope_key, metric, method)`).
- Политика: **в пределах одного листа `id` должен быть уникален** среди строк, которые не являются полностью пустыми (MUST FAIL при дубликате `id` в strict — рекомендуется).

### 4.2 Normalized business key

- **Нормализованный бизнес-ключ** — значение после правил **§5 (Normalization)**, по которому строится **lookup в индексах** (например `partner_key`, `group_key`, `metric_key`, нормализованный `command`, ключ `(job, scope_type, scope_key, param_key)`).
- Два разных `id` с одинаковым нормализованным бизнес-ключом для одной и той же индексируемой сущности и `enabled=1` → **MUST FAIL** (дубликат индекса).

### 4.3 `display_name` и прочие «человеческие» поля

- **`display_name`**, **`title`**, **`note`**, **`comment`**, **`reason`**, **`method_name`** (где применимо) — **представление и документация** для операторов и отчётов.
- Они **не участвуют** в ключе индекса, если явно не сказано обратное (например `display_name` может совпасть с источником для `source_key` в hourly bridge — тогда поведение задаётся листом и §3.10, но ключ факта в отчёте всё равно должен быть согласован с §8.7).

### 4.4 Что участвует в lookup / index

| Сущность | Участвует в индексе / lookup |
|----------|------------------------------|
| Partners | `partner_key`, опционально `partner_code` → маппинг на `partner_key` |
| Groups | `group_key` из `group_name` |
| Limits | `job_key`, `scope_type`, `scope_key`, `metric_key`, `method_key` (nullable) |
| Thresholds | `job_key`, `scope_type`, `scope_key`, `metric_key` |
| Job params | `job_key`, `scope_type`, `scope_key`, `param_key` |
| Access | нормализованный `chat_id` + `user_id` |
| Commands | нормализованный текст команды (`/` + lower по политике §5.5) |
| Command policy | `command_key` |
| Schedules | `schedule_key` / `job_key` (список по job); валидация по §3.7 |
| Report items | `report_key`, `section_key`, `item_key`, `item_type`, `source_key`, `method_key` (по типу item) |

### 4.5 Что используется только для отображения

- Любые поля из §4.3.
- **`style`**, **`notes`** в `ui_layout`, визуальные суффиксы в отчёте.
- **`updated_by` / `updated_at`** (метаданные), если не участвуют в tie-break политике.

### 4.6 Immutable после создания правила (политика V2)

Чтобы не «подменять» сущность под тем же `id`, для **одной и той же строки** (тем же `id` на листе) считается **недопустимым менять семантику идентичности** без осознанного bump контракта:

| Класс полей | Политика |
|-------------|----------|
| **Immutable (не менять произвольно)** | Нормализованный **тип** правила и ключевые оси: `job` / `analyzer`, `scope` + `scope_value` (после нормализации), `metric`, `param_key`, `partner` / `group_name` в роли ключа привязки, `command` как ключ команды, `schedule_type` + идентичность job для schedule. Смена этих полей = **новое правило** (новый `id` или явная процедура миграции в changelog). |
| **Mutable (допускается)** | `limit_value`, `threshold_*`, `enabled`, `reason`/`comment`, `title`/`display_name` там, где не входит в индексный ключ, `default_method`, приоритеты (`group_priority`), временные окна exclusion (если не нарушают политику overlap §8.4). |

**Связь с версиями:** смена immutable-осей при том же `id` в strict → MUST FAIL или требование `deprecated_since` / новой строки (§12).

---

## 5. Normalization contract

### 5.1 Column names

- Trim ASCII/Unicode spaces по краям имени колонки.
- Сравнение имён колонок для валидатора — **после trim**.
- Неизвестные колонки: strict → FAIL, legacy → WARN + ignore.

### 5.2 `partner_key`

- Канон: **`partner_key = build_partner_key(display_name)`** где `build_partner_key`:
  - извлекает числовой **код** из суффикса вида `(123)` если есть;
  - нормализует текстовую часть в ASCII-key (lower, translit, спецсимволы → `_`);
  - склеивает `base_code` или только код.
- Любой ввод из операционных файлов (payout/payin) для поиска правил должен проходить **тот же канон** (не смешивать «сырое» `normalize_key` всей строки без правил `build_partner_key`).

### 5.3 `group_key`

- `group_key = normalize_key(group_name)` из листа `partner_groups` / `wallet_limits` scope=group.
- Отображаемое имя хранится отдельно (`display_name` группы).

### 5.4 `method_key`

- Строка метода: **trim**, **lower** для индекса/lookup (например `uni`, `sbp`).
- Пустая строка в данных payout: может подставляться **default_method** (allowed fallback), иначе ошибка job (wallet).

### 5.5 Command text

- Хранится в Excel без обязательного `/`; при индексации — **ведущий `/`**, затем lower для карты политик (если политика такова); команда как текст для пользователя может отображаться исходно.

### 5.6 `scope_type` / `scope_key`

- `scope_type` ∈ {`global`, `group`, `partner`} после `normalize_key`.
- `scope_key`: для `global` — `*`; для `group` — `group_key`; для `partner` — **`partner_key`** (канон).

### 5.7 `metric_key`

- `metric_key = normalize_key(metric)` из thresholds / limits.

### 5.8 Datetime

- Ввод в Excel может быть datetime или строка; парсинг — **Europe/Moscow** (как в коде проекта), неизвестное → MUST FAIL в strict для обязательных полей.

### 5.9 Booleans

- `enabled`, `allow_private`, `allow_groups`, флаги: **0/1**, а также согласованный набор строк (`true/false/yes/no` — если поддерживается в bridge, иначе MUST FAIL).

### 5.10 Numeric values

- Десятичный разделитель: **`.` или `,`** на этапе parse — единая политика в bridge; невалидное → MUST FAIL для обязательных числовых полей.

---

## 6. Layer responsibility model

| Layer | Responsibility |
|-------|----------------|
| **Excel** | Бизнес-конфигурация: значения, включение/выключение, приоритеты (когда введены в лист), человеко-читаемые поля. |
| **Bridge** | **Только нормализация:** типы, trim, канонические ключи, сборка структур snapshot **без** бизнес-решений «какой лимит выбрать». Не дублировать precedence accessors. |
| **Validators** | **Contract enforcement:** MUST FAIL / WARN по этому документу; дубликаты; ссылки; overlap; неизвестные колонки (strict). |
| **Indexes** | **Детерминированный lookup:** один ключ → одна сущность (после валидации); порядок коллекций при множественных правилах на один `job` задаётся **явной сортировкой по политике** (§13), а не случайным порядком строк Excel. |
| **Accessors** | **Precedence resolution:** partner > group > global; method-specific > generic; выбор exclusion по политике §8.4. Без IO. |
| **Analyzers** | **Бизнес-расчёты** по уже загруженным данным (файлы payout/payin и т.д.) и **готовым** правилам из snapshot/accessor. Не переопределяют контракт правил. |
| **Reporters** | **Только presentation:** маппинг DTO → текст/макет по согласованным ключам; без нового semantic resolve правил. |

### 6.1 Правило для analyzers / reporters

- **Запрещено** в analyzers/reporters **самостоятельно** «дорезолвить» лимиты, пороги, exclusions, job params или менять семантику (например дублировать precedence partner/group/global, подменять `partner_key`, вводить второй fallback method вне явного контракта §7.3).
- Допустимо: вызывать **публичный API** accessors / передавать уже вычисленные поля из DTO; форматирование чисел, строк, порядок секций **по snapshot** `report_items` / `ui_layout`.
- Если для отчёта не хватает данных — **ошибка валидации раньше** или явный WARN на этапе сборки snapshot, а не «тихая подмена» в reporter.

---

## 7. Validation policy

### 7.1 MUST FAIL

- Дубликаты **индексных ключей** для enabled-строк (см. §9 список ключей); в отчёте валидатора — коды из **§18**.
- Невалидная строка **schedules** для enabled (тип, пустой cron, non-positive interval).
- Неизвестные **`scope` / `schedule_type`**.
- Строгий режим: **любая неизвестная колонка** на листе.
- Конфликтующие **command policies** на один `command_key`.
- **Orphan references:** limit/threshold на partner/group, отсутствующих в справочнике (если включено правило строгой ссылочной целостности).
- **Overlapping exclusions** — если выбрана политика §8.4 «запрет пересечений».
- Нарушение **immutable-осей** (§4.6) при попытке представить как «ту же» строку — в strict (политика внедрения).

### 7.2 WARN

- `meta.version` не совпадает с ожидаемой для ветки (до миграции).
- Deprecated колонки / пустые строки-мусор.
- Дублирующиеся **неиндексируемые** данные (например два note).
- Fallback method `None` сработал после specific miss (если включён диагностический флаг).
- Primary group выбран по **порядку строк** (нет `priority` / `is_primary`) — **deprecated** (§13).

### 7.3 ALLOWED FALLBACK

- **Limit resolution:** для фиксированного scope сначала лимит с `method_key`, затем с `method_key=null` (generic) — **явный** допустимый fallback (§8.2).
- **Job param:** отсутствие ключа → значение `default` в коде вызывающей стороны — только для параметров, помеченных как необязательные в спецификации job.
- **Partner resolution:** попытка по **partner_code** из скобок, затем по полному канону — **явный** допустимый порядок (§5.2).
- **Hourly report:** отсутствие факта в DTO для строки конфига → **0** и пустой комментарий — допустимо **только** если включён режим «compat display»; иначе WARN/MUST FAIL по политике релиза.

### 7.4 LEGACY COMPATIBILITY

- Trim имён колонок `job_params`.
- Игнор полностью пустых строк на hourly листах.
- Пока нет `group_priority`: primary group = первая подходящая membership — **с WARN** (§13).
- `meta.version` < целевого V2: валидатор может работать в режиме **legacy** (без strict колонок).

---

## 8. Precedence rules

### 8.1 Scope: `partner > group > global`

Для **`get_job_param`**, **`resolve_limit_rule`**, **`resolve_threshold_rule`** порядок попыток:

1. `(scope_type=partner, scope_key=partner_key)` если `partner_key` задан.
2. `(scope_type=group, scope_key=group_key)` если `group_key` задан.
3. `(scope_type=global, scope_key=*)`.

Если на одном уровне найдено **более одного** кандидата с одинаковым приоритетом — **MUST FAIL** при валидации (индекс должен гарантировать единственность).

### 8.2 Method: `method-specific > generic`

Для **`resolve_limit_rule`** внутри каждого scope:

1. Правило с `method_key == requested_method_key` (нормализованным).
2. Правило с `method_key is null` (generic).

Если оба существуют и конфликтуют по значению — не должно происходить: **дубликат ключа** в индексе MUST FAIL.

### 8.3 Primary group selection

**Целевое V2:**

1. Если задано **`is_primary=1`** у membership — выбирается эта строка.
2. Иначе минимальный **`group_priority`** (число).
3. Иначе стабильная сортировка по `(group_key, id)` и WARN «не задан priority».

**До появления полей в Excel — legacy:** первая membership в порядке строк snapshot + WARN (**зависимость от порядка строк — deprecated**, §13).

**Инвариант (нормативно для кода):** *If no explicit primary metadata is provided, membership ordering must remain identical to snapshot insertion order.* Регрессионные тесты фиксируют, что при двух группах на одного партнёра порядок `[A, B]` в snapshot даёт `[A, B]` у accessor, а перестановка строк на `[B, A]` даёт `[B, A]`; в обоих случаях валидация по-прежнему эмитит `RULE_NON_DETERMINISTIC_ORDER` до миграции на явные поля (см. ``tests/rules_v2/test_membership_order_legacy.py``).

### 8.4 Exclusion overlap behavior

**Вариант A (рекомендуемый strict):** пересечение интервалов для одного `(job_key, partner_key)` → MUST FAIL при валидации.

**Вариант B (операционный):** поле `priority` (int); при пересечении выбирается **минимальный priority**; при равенстве — MUST FAIL.

**Запрещено в V2:** «первый в файле без объявленного правила» без WARN/strict flag.

### 8.5 Command / access policy resolution

- Одна запись **access** на пару `(chat_id, user_id)` после нормализации.
- Одна **policy** на `command_key`.
- Разрешение команды: найти команду по тексту → policy → `min_role_key` → уровень из `roles`.

### 8.6 Schedule selection

- В runtime используются только строки с `enabled=1` и валидной парой (`schedule_type` + параметры).
- Несколько расписаний на один `job_type` — **допускаются**; порядок исполнения/отображения определяется **явным** `sort_order` / `priority` на листе; при отсутствии — стабильная сортировка по `(schedule_key, id)` и **WARN** (не сырой порядок Excel без документа — §13).

### 8.7 `ui_layout` / `report_items` matching

- **`section_key`** в snapshot: `{report_key}.{section_key}` с нормализацией компонент (как в bridge/reporter).
- **Hourly facts:** ключи в DTO (`entity_code`, `group_code`, `(group_code, method)`) **должны** совпадать с `source_key` / `method_key` report items для строкового вывода; рассинхрон — WARN или MUST FAIL (strict reports).

---

## 9. Runtime invariants

После успешной сборки `RulesSnapshotV2` и `build_indexes` в **strict V2** должно выполняться:

1. **Нет дубликатов индексных ключей** для enabled сущностей (params, limits, thresholds, access, commands/policy).
2. **Нет orphan ссылок** на partner/group (если включена ссылочная проверка).
3. **Детерминированный primary group** (по §8.3 и §13).
4. **Детерминированное разрешение exclusions** (по §8.4 и §13).
5. **Стабильная command policy** (один policy на command_key).
6. **Report items:** каждый `parent_group_item` / ссылочный member указывает на существующий `item_key` (если тип member подразумевает ссылку).
7. **Schedules:** каждая enabled-строка валидна до запуска scheduler; невалидные не попадают в runtime **и** не молчат в strict (ошибка валидации раньше).

Дополнительные гарантии исполнения см. §14.

---

## 10. Failure modes (запрещённые silent failures)

| Failure | Описание | Контрактное действие |
|---------|----------|----------------------|
| Duplicate overwrite | Две строки → один индекс-ключ | MUST FAIL §7 |
| Partner canonicalization mismatch | Разные ключи из одного бизнес-имени | Единый §5.2 + тесты §15 |
| Hidden fallback | Generic limit без явной политики | Документировать как ALLOWED §7.3 + опционально WARN |
| Missing layout key | В `ui_layout` нет строки для обязательного ключа отчёта | WARN/MUST FAIL по политике view |
| Empty report block | Данные есть, но ключи не сматчились → 0 | Golden tests + strict |
| Wrong method fallback | Ожидали specific, применился generic незаметно | Дубликаты запрещены; диагностический WARN |
| Skipped schedule | Битая строка отброшена | MUST FAIL в strict при enabled |
| Overlapping exclusions | Недетерминизм | §8.4 |

---

## 11. Migration compatibility

### 11.1 Что поддерживаем из legacy

- Текущий набор листов и имён колонок (после trim).
- Порядок строк как слабый tie-breaker **временно** для primary group и exclusions — **только с WARN** и пометкой deprecated (§13).
- `method_key=None` как generic лимит после specific — **явный allowed fallback**.
- Частичное отсутствие колонки `enabled` на отдельных hourly листах — только в legacy режиме.

### 11.2 Что deprecated

- Лишние столбцы Excel (`Unnamed:*`, дублирующие `cron.*`) — не использовать; удалить из файлов.
- Неявный выбор «первая строка wins» без `priority` / `is_primary` — deprecated (§13).
- Зависимость runtime от **сырого** порядка строк Excel без явной сортировки — deprecated.

### 11.3 Сейчас WARN, позже MUST FAIL

- Любые неизвестные колонки.
- Пересекающиеся exclusions без `priority` (если не выбран вариант A §8.4).
- Отсутствие колонок `group_priority` / `is_primary` при множественных membership.
- Несовпадение `meta.version` с целевой для ветки.
- Граф deprecation по §12 (`deprecated_since` / `removed_in`).

### 11.4 Strict mode

- Флаг окружения или CLI, например **`RULES_CONTRACT_STRICT=1`** или `validate_rules_xlsx.py --strict`.
- В strict: все MUST FAIL из §7.1; unknown columns → FAIL; skipped schedules → FAIL; overlap policy A или B обязательна.

---

## 12. Version migration policy

### 12.1 Переход `meta.version = N` → `N+1`

1. **Изменение схемы** (лист, колонка, семантика MUST/WARN, precedence, индексный ключ) → оформить в этом документе + запись в **Document history** в конце файла.
2. Поднять **`meta.version`** в эталонном `rules.xlsx`.
3. Обновить **валидатор** и диапазон поддерживаемых версий в CI/runtime.
4. Прогнать **тесты §15** на фикстурах и на копии prod-правил.
5. Релиз кода и файла согласованы: **ниже минимальной поддерживаемой версии** файл отклоняется (MUST FAIL в strict).

### 12.2 Поля deprecation в листах (рекомендуемые)

Для строк, которые устарели, но ещё читаются:

| Поле | Смысл |
|------|--------|
| **`deprecated_since`** | Версия контракта `meta.version`, начиная с которой использование считается устаревшим → **WARN**. |
| **`removed_in`** | Версия, начиная с которой колонка/строка **не читается** → MUST FAIL, если всё ещё присутствует. |

Если колонок нет в текущем Excel — deprecation задаётся **только документом** (§11.3) до появления колонок в шаблоне.

### 12.3 Окно обратной совместимости

- Для изменения, ломающего старые файлы: минимум **один релиз**, в котором поведение **WARN + совместимый fallback** (если безопасно), затем релиз с **MUST FAIL**.
- Критичные security-изменения (`access`/`commands`) — окно может быть **0** (сразу MUST FAIL) по решению владельца контракта.

### 12.4 Когда WARN становится MUST FAIL

- По достижении версии **`removed_in`** для фичи/колонки.
- По включению **strict mode** (§11.4) для выбранного класса проверок.
- По календарю: дата «cutoff» фиксируется в changelog релиза (опционально).

### 12.5 Кто отвечает за bump версии

| Роль | Ответственность |
|-------|-----------------|
| **Owner контракта** (в `meta`/документе) | Решение о bump `meta.version`, утверждение MUST/WARN, сроков deprecation. |
| **Разработчик** | Синхронизация валидатора, bridge (только нормализация), тестов; не менять precedence в analyzers без §6. |
| **Оператор** | Обновление prod `rules.xlsx`, прохождение валидатора до merge в Dropbox/источник. |

---

## 13. Deterministic ordering rule

### 13.1 Базовое правило

- **Runtime не должен зависеть от порядка строк Excel** для выбора семантики (какое правило активно, какая primary group, какое exclusion «главное», какой tie-break при равных приоритетах).

### 13.2 Допустимые исключения

Исключения **допустимы только если явно описаны** в этом документе, например:

- **Явное поле сортировки** (`sort_order`, `priority`, `is_primary`) — порядок задаётся данными, а не файлом.
- **Стабильная сортировка по вторичным ключам** `(id, schedule_key, group_key, …)` как временная мера — должна быть названа в §8 / §11 и сопровождаться **WARN**, пока не введены явные поля приоритета.

### 13.3 Legacy: порядок строк

- Поведение «первая подходящая строка в порядке загрузки» помечается как **WARN + deprecated** и должно быть устранено введением явных полей или сортировки §13.2.
- В strict V2 такое поведение **не является целевым** и подлежит удалению после окна совместимости (§12.3).
- **Primary group / memberships:** пока нет явных полей приоритета, runtime **обязан** сохранять порядок списка в snapshot (см. §8.3 инвариант); это *намеренное* legacy-поведение, а не недоработка stable sort — автоматическая пересортировка без колонок меняла бы prod без миграции.

---

## 14. Runtime guarantees

Ниже — целевые гарантии исполнения (даже если текущий код ещё не полностью им соответствует — это **target** для реализации). **Жизненный цикл публикации и reload снимка** — §17.

### 14.1 Snapshot immutable after build

- После успешного `build_snapshot` объект **`RulesSnapshotV2` не мутирует** поля правил в ходе job.
- Любое обновление правил → новый snapshot (новая загрузка / новый stat_key).

### 14.2 Accessors pure, без IO

- Методы accessors **не выполняют** сеть, диск, env-read внутри resolve; только чтение переданного snapshot + indexes.

### 14.3 Indexes deterministic

- Для одинакового валидного входного snapshot результат **`build_indexes` идентичен** (порядок итерации по стабильно отсортированным коллекциям или по детерминированным ключам).

### 14.4 Validation before runtime

- Job **не стартует** при MUST FAIL валидации в strict; в legacy — политика WARN + risk acceptance должна быть явной (вне scope автоматического silent).

### 14.5 No silent mutation during job execution

- В процессе выполнения job **не изменяются** правила, индексы, policies «по ходу» без явного события reload; кэш snapshot инвалидируется только осознанным механизмом (TTL, команда оператора, новый файл).

---

## 15. Test requirements

Обязательные группы тестов (минимум):

1. **Snapshot build:** фикстура workbook → ожидаемое число сущностей, ключевые поля `MetaInfo`, наличие hourly `ReportDef`.
2. **Validation:** каждый класс MUST FAIL из §7.1 даёт предсказуемое сообщение.
3. **Duplicate detection:** две строки `job_params` / `limits` / `thresholds` с одним ключом → ошибка в strict.
4. **Accessor precedence:** матрица partner/group/global × method specific/generic; стабильный результат.
5. **Partner canonicalization:** таблица входов (raw имена) → один `partner_key`; согласованность с `resolve_partner`.
6. **Schedule validation:** валидные / невалидные строки; strict запрещает silent skip.
7. **Golden report tests:** фикстурные payin/payout + rules → эталонные строки hourly (и критичный wallet блок).
8. **Determinism:** перестановка строк Excel без изменения данных и приоритетов → идентичный индекс/результат resolve (после внедрения §13).

---

## 16. Implementation constraints

Этот раздел фиксирует **ограничения на изменение кода** до завершения согласованной миграции.

### 16.1 Stage 1 — только validation layer

- **Stage 1** меняет **только** слой валидации (`tools/validate_rules_xlsx.py`, расширение `core/rules_v2/validators.py` или отдельный модуль проверок), в соответствии с §7 / §12 / §13 / §18.
- Цель Stage 1: MUST FAIL / WARN на копии реальных файлов **без** изменения бизнес-результатов job. Дорожная карта стадий — **§22**.

### 16.2 Runtime behavior в Stage 1 не менять

- Поведение bridge/accessors/analyzers/reporters **не меняется** семантически до явного **Stage 2+** и отдельного design approval.

### 16.3 Остальные слои — только с отдельным design approval

- **`accessors` / `indexes`:** любое изменение precedence или детерминизма — отдельное ревью (ссылка на §6, §8, §13, §14).
- **`analyzers` / `reporters`:** правки только если они **не** вводят новый resolve правил (§6.1); иначе — design approval + обновление этого документа.

### 16.4 Rollback

- Любой Stage 2+ за feature flag или версией `meta.version`; откат = предыдущий коммит + предыдущий `rules.xlsx`.

### 16.5 Acceptance criteria для выхода из Stage 1

- Валидатор покрывает все MUST FAIL из §7.1, применимые к текущему файлу.
- Документ CONTRACT_V2 и валидатор согласованы (чеклист ревью).
- На эталонной копии prod-правил нет неожиданных MUST FAIL без плана миграции строк.

---

## 17. Snapshot lifecycle model

Цель — устранить неявную смену правил mid-job и зафиксировать, **когда** снимок считается действующим и как безопасно обновлять его.

### 17.1 Когда snapshot считается **active**

- Snapshot **`RulesSnapshotV2` + `RulesIndexes`** считается **active** для runtime после одновременного выполнения:
  1. успешной загрузки workbook (или кэшированной копии с известным `stat_key` / fingerprint);
  2. **успешной валидации** по политике режима (strict: без blocking-кодов §18 severity `error`; legacy: допускаются только `warn`/`info` на путь запуска — политика продукта);
  3. успешной сборки snapshot и индексов (bridge после валидации или в порядке, зафиксированном реализацией Stage 2+).
- До выполнения п.2–3 снимок **не публикуется** в слой, от которого зависят job (см. §17.4).

### 17.2 Reload (инвалидация и перезагрузка)

- **Триггеры reload:** изменение источника файла (mtime/size/content hash), истечение TTL кэша, явный `force_sync`, операторская команда «перечитать rules», смена ветки/окружения.
- **Порядок:** загрузка сырья → валидация → build snapshot/indexes → **только затем** публикация (см. atomic swap §17.4).

### 17.3 Можно ли job завершать на «старом» snapshot

- **Рекомендуемая политика (целевая):** каждый job **закрепляет (pins)** пару `(snapshot, indexes)` **на время выполнения** с момента старта или первого чтения правил в job; **reload не подменяет** закреплённый снимок до завершения job.
- **Альтернатива (операционная):** прерывание job при обнаружении нового файла — только если явно включено политикой продукта (не default для длинных job).
- Короткие операции могут каждый раз читать «текущий active»; длинные — обязаны pin (§20 snapshot reused = кэш на процесс, не mid-job swap без политики).

### 17.4 Atomic snapshot swap

- Публикация нового active-snapshot — **атомарная замена указателя** (одна операция присвоения / swap структуры в кэше) после того, как новый объект **полностью** собран и провалидирован.
- Потребители читают либо старый, либо новый снимок целиком; **не существует** «частично обновлённого» снимка для читателей.

### 17.5 Invalidation policy

- Любой триггер §17.2 помечает текущий кэш **stale**; следующий запрос **не** отдаёт новый снимок до завершения цикла load→validate→build.
- Несовпадение `meta.version` с поддерживаемым диапазоном (§19) → **не публиковать** новый снимок; active остаётся предыдущий успешный **или** отказ в запуске новых job (политика: fail-closed для strict).

### 17.6 Rollback snapshot behavior

- **Откат файла:** возврат предыдущего `rules.xlsx` + **invalidate** кэша → следующий load строит снимок из откатанного файла.
- **Откат кода:** предыдущий бинарь/ветка с другим диапазоном `meta.version` — при несовместимости файла новый снимок не строится (§19).
- Опционально хранить **последний известный good** snapshot read-only для диагностики (не для автоматического прод-исполнения без явного переключения).

---

## 18. Structured validation and error model

Валидатор (Stage 1+) выдаёт структурированные записи: **`code`**, **`severity`**, **`sheet`**, **`row`/`id` (если есть)**, **`message`**, опционально **`field`**.  
Ниже — нормативный каталог кодов; расширение кодов допускается только с обновлением этого документа и Document history.

| Code | Severity | Description | Strict behavior | Legacy behavior |
|------|----------|-------------|-----------------|-----------------|
| `RULE_DUPLICATE_LIMIT` | error | Две enabled-строки `wallet_limits` с одинаковым индексным ключом (§3.4, §7.1). | MUST FAIL pipeline / не публиковать snapshot. | WARN; snapshot может строиться как сейчас (последняя строка побеждает) до включения strict — **deprecated**. |
| `RULE_DUPLICATE_THRESHOLD` | error | Дубликат индексного ключа `thresholds_partner`. | MUST FAIL / не публиковать. | WARN + deprecated overwrite. |
| `RULE_DUPLICATE_JOB_PARAM` | error | Дубликат `(job, scope, scope_key, param_key)` в `job_params`. | MUST FAIL / не публиковать. | WARN + deprecated. |
| `RULE_DUPLICATE_ACCESS` | error | Дубликат `(chat_id, user_id)` в `access`. | MUST FAIL / не публиковать. | WARN + deprecated. |
| `RULE_DUPLICATE_COMMAND` | error | Две enabled-команды с тем же нормализованным ключом. | MUST FAIL / не публиковать. | WARN + deprecated. |
| `RULE_DUPLICATE_COMMAND_POLICY` | error | Несколько конфликтующих политик на один `command_key`. | MUST FAIL / не публиковать. | WARN + deprecated. |
| `RULE_DUPLICATE_SCHEDULE_ID` | error | Два enabled расписания с одним `id` (если `id` объявлен уникальным для листа). | MUST FAIL. | WARN (если runtime мержит по последнему — задокументировать WARN). |
| `RULE_INVALID_SCHEDULE` | error | `schedule_type` / `every_seconds` / `cron` не удовлетворяют §3.7. | MUST FAIL / не публиковать при enabled. | WARN или skip строки — **deprecated**; цель — WARN с кодом, затем MUST FAIL в strict. |
| `RULE_ORPHAN_PARTNER` | error | Ссылка на partner/group в limit/threshold/param при включённой ссылочной целостности (§7.1). | MUST FAIL. | WARN или ignore — только явная политика legacy. |
| `RULE_ORPHAN_GROUP` | error | Ссылка на неизвестную группу при strict ссылочности. | MUST FAIL. | WARN / policy. |
| `RULE_OVERLAPPING_EXCLUSION` | error | Пересечение exclusion-интервалов при политике §8.4 вариант A. | MUST FAIL. | WARN + недетерминизм запрещён в целевом V2 (вариант B с `priority`). |
| `RULE_UNKNOWN_COLUMN` | error | Лист содержит столбец вне схемы (§3, strict). | MUST FAIL. | WARN + игнор столбца. |
| `RULE_MISSING_SHEET` | error | Лист, обязательный для текущей версии контракта (§3), отсутствует в workbook. | MUST FAIL pipeline / не публиковать snapshot. | error для обязательных листов; для известных опциональных листов допустим явный override severity до warn (политика валидатора). |
| `RULE_MISSING_COLUMN` | error | На присутствующем листе нет обязательной колонки из §3 (после trim §5.1). | MUST FAIL / не публиковать. | MUST FAIL; в legacy допустим override severity до warn для отдельных колонок с задокументированным fallback (например `enabled` у hourly листов — §11.4, §3.10). |
| `RULE_DEPRECATED_COLUMN` | warn | Лист содержит колонку, объявленную deprecated в §3 (например `Unnamed:*`, `cron.1` для §3.7), или помеченную `deprecated_since` (§12.2). | WARN; элевейт до error по `removed_in` (§12.2). | WARN. |
| `RULE_INVALID_SCOPE` | error | `scope` / `scope_type` не из разрешённого набора (§5.6, §3.4). | MUST FAIL. | MUST FAIL или WARN по политике листа. |
| `RULE_INVALID_SCHEDULE_TYPE` | error | `schedule_type` ∉ {`interval`, `cron`}. | MUST FAIL. | WARN + skip (deprecated). |
| `RULE_INVALID_META_VERSION` | error / warn | `meta.version` вне поддерживаемого диапазона для данного runtime (§19). | error: не публиковать / не стартовать. | warn: допускается только при явном risk acceptance. |
| `RULE_IMMUTABLE_ID_VIOLATION` | error | Смена immutable-осей при том же `id` без миграции (§4.6). | MUST FAIL в strict audit. | WARN. |
| `RULE_NON_DETERMINISTIC_ORDER` | warn | Разрешение зависит от порядка строк без явного priority (§13). | WARN (обязательно в strict validation pass). | WARN. |
| `RULE_VALIDATION_INFO` | info | Информационные сообщения (например «файл валиден», счётчики). | Не блокирует. | Не блокирует. |

**Инвариант:** один blocking `error` в strict → **нет** публикации active-snapshot для новых job (§17.1).

---

## 19. Runtime and contract compatibility matrix

Матрица задаёт **ожидаемый статус** сочетания ветки runtime (возможности кода) и **`meta.version`** workbook. Конкретные номера версий пополняются при bump (§12).

| Runtime | `meta.version` | Status | Примечание |
|---------|----------------|--------|------------|
| **V2 rules runtime** (snapshot + bridge v2) | **3** | **compat** | Поддерживаемый режим: валидатор Stage 1 может выдавать WARN по устаревшим конструкциям; семантика согласована с prod-наследием. |
| **V2 rules runtime** | **4** (пример следующего контракта) | **native** | Целевая схема §3–§18 без legacy-исключений, strict по умолчанию в CI. |
| **V2 rules runtime** | **< min_supported** | **unsupported** | MUST FAIL (`RULE_INVALID_META_VERSION`); не публиковать snapshot. |
| **Legacy runtime** (без полного snapshot/контракта) | **3** | **legacy** | Исторический режим; не целевой. |
| **Legacy runtime** | **4+** | **unsupported** | Новый файл не должен обслуживаться старым runtime без апгрейда кода. |

**Правило:** строка матрицы для каждого релиза кода должна быть задокументирована в release notes; расхождение с таблицей — дефект процесса.

---

## 20. Performance and execution guarantees

Краткие **целевые** гарантии (согласуются с §14, §17):

1. **Validation before runtime:** блокирующая валидация strict выполняется до публикации active-snapshot (§14.4, §17.1).
2. **Snapshot reused across jobs / requests:** допустим общий кэш `(snapshot, indexes)` с TTL и fingerprint файла; job **pin**-ит ссылку на время выполнения (§17.3).
3. **Indexes intended O(1) lookup:** словари по составным ключам; без линейного скана по всем правилам в hot-path accessors (целевая архитектура).
4. **Deterministic `build_indexes`:** один и тот же валидный snapshot → бит-в-бит идентичные индексы (порядок обхода стабилен) — §14.3, §13.
5. **Validator must not mutate workbook data:** валидация **read-only** по содержимому файла (копия в памяти / temp read); запись в `rules.xlsx` только отдельными инструментами миграции, не валидатором Stage 1.

---

## 21. Source of truth

### 21.1 Таблица слоёв

| Layer | Source of truth |
|-------|-----------------|
| **Excel (`rules.xlsx`)** | Бизнес-намерение: значения, включения, приоритеты (когда введены), человеко-читаемые поля. |
| **CONTRACT_V2.md (+ CONTRACT_RULES.md)** | Нормативная семантика: схема, валидация, precedence, lifecycle, коды ошибок, матрица совместимости. |
| **Validators** | Enforcement: соответствие Excel ↔ контракт; **не** подмена бизнес-решений. |
| **Bridge** | Только нормализация и маппинг полей в snapshot-модель по контракту; **не** источник бизнес-правил. |
| **Indexes** | Детерминированное отображение «ключ → сущность» из уже валидного/согласованного snapshot. |
| **Accessors** | Precedence и композиция правил из индексов; **не** изменение значений правил. |
| **Runtime job config (env, flags)** | Только **режимы** (strict, TTL, min_supported version), а не содержимое лимитов/порогов. |

### 21.2 Явные правила

- **Excel хранит business intent** — что включено, какие лимиты/пороги, кто имеет доступ.
- **Bridge не меняет semantics** — не «улучшает» и не исправляет ошибки оператора; максимум нормализация представления (§6).
- **Runtime не «исправляет» плохие rules** — не подставляет лимиты по догадке, не удаляет дубликаты молча; ошибки → валидатор или fail-closed (§7, §18).
- **Validators — единственное место enforcement** контракта для файла перед публикацией снимка (Stage 1); runtime-код не дублирует полный набор MUST FAIL без синхронизации с §18.

---

## 22. Stage roadmap

### Stage 1 — validation layer only

- Реализация §18 (коды, severity), read-only проверка файла, CLI/CI интеграция.
- Без изменения семантики bridge/accessors/analyzers/reporters (§16).
- Выход: все MUST FAIL из §7.1 покрыты кодами; prod-файл проходит в legacy-режиме с планом WARN→FAIL.

### Stage 2 — deterministic runtime

- Индексы и accessors: детерминизм §13–§14, §17 atomic swap + pin; устранение зависимости от порядка строк без явных полей (где требуется — новые колонки в Excel + bump версии §12).
- Bridge: только то, что нужно для детерминизма и контракта, без смены бизнес-формул в analyzers.

### Stage 3 — strict runtime + legacy cleanup

- Strict по умолчанию в prod для поддерживаемых `meta.version`; удаление deprecated путей (silent overwrite, skip schedule без кода ошибки).
- Сужение матрицы §19 до **native** только для актуальной версии; повышение `min_supported`.

**Связь с §16:** ограничения Stage 1 остаются обязательными до закрытия Stage 1 acceptance; переход на Stage 2/3 — отдельное решение и обновление документа.

---

## 23. Explainability (C11)

Нормативное разделение слоя объяснимости от runtime-resolve. **Семантика**
production-resolve **не меняется** C11; только контракты и опциональный replay.

### 23.1 C11.1 — trace contracts

- Стабильные имена операций / фаз и структура шага трассировки (например
  ``ResolutionTraceStep`` в ``core.rules_v2.explain.types``): значения в
  ``inputs`` ограничены JSON-примитивами (``JsonPrimitive``); сериализация для
  тестов — ``trace_step_to_jsonable`` / ``trace_steps_to_jsonable``.
- Назначение: единый формат для тестов, будущей диагностики и документации
  без привязки к provider / audit / analyzers.

### 23.2 C11.2 — explicit replay API

- Явный **read-only replay** выбранных путей разрешения (в т.ч. partner, limit,
  threshold, job param — см. §23.5), выровненный по порядку и ключам с
  ``BaseRulesAccessor`` / ``RulesIndexes``.
- **Explicit opt-in:** replay вызывается только явным импортом и вызовом
  ``explain_*``; production resolve **не** оборачивается автоматически. Это
  **не** runtime-инструментизация.

### 23.3 Runtime instrumentation

- **Пока отсутствует:** обычные пути resolve в проде **не** обязаны и **не**
  должны неявно генерировать полные trace-цепочки только из-за наличия C11.
- Replay и контракты трассировки — вспомогательный слой вне hot path.

### 23.4 Зависимости и импорт пакета ``explain``

- **Пакет по умолчанию** (лёгкий импорт типов / констант) остаётся без тяжёлых
  транзитивных зависимостей там, где это зафиксировано реализацией (см. модуль
  ``core.rules_v2.explain``).
- **Replay** может тянуть цепочку нормализации (в т.ч. транзитивно **pandas**);
  это допустимо, т.к. replay вне production hot path и не меняет результат
  resolve — только воспроизводит его как шаги.

### 23.5 C11.3 — threshold и job_param replay

- Явный replay для ``resolve_threshold_rule`` и ``get_job_param`` (только
  индексы; ключи как в ``RulesIndexes``; финальный шаг ``PHASE_RESULT``
  обязателен).
- Для ``get_job_param`` в ``inputs`` шагов **нет** произвольных значений
  параметра и **нет** сериализации ``default``; на финальном ``PHASE_RESULT``
  только ``value_source`` (**snapshot** | **default**), см. реализацию
  ``explain_get_job_param``.

---

## Document history

| Version | Date | Notes |
|---------|------|-------|
| V2 design | 2026-05-11 | Initial spec + pass 2: entity identity (§4), layer model (§6), version migration (§12), deterministic ordering (§13), runtime guarantees (§14), implementation constraints (§16); full renumber §1–§16. |
| V2 design | 2026-05-11 | Pass 3 (architecture): §17 Snapshot lifecycle, §18 Error codes, §19 Compatibility matrix, §20 Performance guarantees, §21 Source of truth, §22 Stage roadmap; cross-refs §2, §7, §16. |
| V2 design | 2026-05-12 | Stage 1 / C2: §18 расширен тремя кодами для workbook schema validation — `RULE_MISSING_SHEET`, `RULE_MISSING_COLUMN`, `RULE_DEPRECATED_COLUMN`. Семантика runtime не меняется; коды используются исключительно валидатором workbook (lib-only в C2). |
| V2 design | 2026-05-12 | C11: добавлен §23 Explainability — C11.1 trace contracts, C11.2 explicit replay API, отсутствие обязательной runtime-инструментизации; политика лёгкого импорта пакета explain vs допустимые транзитивные зависимости replay. |
| V2 design | 2026-05-13 | C11.3: §23.5 — replay для ``resolve_threshold_rule`` и ``get_job_param``; трассировка job_param без произвольных значений в ``inputs``. |
| V2 design | 2026-05-13 | C11 stabilization: §23.2/§23.5 уточнены под текущий replay; explicit opt-in / JSON-safe trace / no runtime instrumentation. |
| V2 design | 2026-05-13 | Hourly presentation flags: `job_params.hide_inactive_rows` (bool, default false, presentation-only) + section spacing (одна пустая строка перед payouts/payins в тексте hourly без смены порядка `ui_layout`) — §3.8.1–§3.8.2. |
| V2 design | 2026-05-13 | §3.13 / §8.3 / §13.3: явный инвариант legacy — без `is_primary` / `group_priority` порядок membership в runtime = порядок вставки в snapshot; регрессионные тесты против silent stable sort; таблица §3.13 дополнена опциональными колонками. |
