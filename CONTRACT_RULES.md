Contract version: 1  
Last updated: 17.01.2026  
Owner: Ostin  
Scope: rules.xlsx (Control Plane)

### 1. Назначение документа

#### Этот документ фиксирует контракт правил, используемых системой.

Цель контракта:
 * сделать поведение системы воспроизводимым
 * исключить “тихие” изменения логики
 * разделить ответственность между:
    * оператором (что можно менять)
    * системой (как это применяется)

  rules.xlsx — источник операторских решений  
  YAML-конфиги — описание структуры и логики анализаторов  
  Код — исполнитель контракта, но не его источник  
  
### 2. Общая модель правил

#### 2.1 Активность правила:
 * Любое правило имеет поле enabled
 * enabled = 1 → правило активно
 * enabled = 0 → правило полностью игнорируется
 * Отключённые правила не участвуют в расчётах

#### 2.2 Принцип уникальности

Для каждого типа правил определён ключ уникальности.  
Для каждого ключа может существовать ровно одно активное правило.

_Нарушение → ошибка конфигурации, расчёты не запускаются._

### 3. Структура rules.xlsx

#### 3.1 Лист meta

Используется для версионирования и совместимости.

Обязательные строки:

| key              | value      |
| ---------------- | ---------- |
| contract_version | 1          |
| updated_at       | 17.01.2026 |

#### 3.2 Лист exclude_time  

Назначение: исключить временные интервалы из расчётов.

| column     | type      | required |
| ---------- | --------- | -------- |
| id         | string    | yes      |
| enabled    | int (0/1) | yes      |
| analyzer   | string    | yes      |
| partner    | string    | no       |
| card       | string    | no       |
| pool       | string    | no       |
| start_dt   | datetime  | yes      |
| end_dt     | datetime  | yes      |
| reason     | string    | yes      |
| created_by | string    | no       |
| created_at | datetime  | no       |

Правила применения:
 * Интервалы применяются по OR-логике
 * Если событие попадает хотя бы в один активный интервал → исключается
 * start_dt < end_dt — обязательное условие

#### 3.3 Лист thresholds_partner  

Назначение: пороги для метрик анализаторов.

| column     | type      | required |
| ---------- | --------- | -------- |
| id         | string    | yes      |
| enabled    | int (0/1) | yes      |
| analyzer   | string    | yes      |
| partner    | string    | yes      |
| metric     | string    | yes      |
| threshold  | number    | yes      |
| min_events | number    | no       |
| reason     | string    | yes      |
| updated_by | string    | no       |
| updated_at | datetime  | no       |

Ключ уникальности:  
(analyzer, partner, metric)

Для каждого ключа:
* 0 активных → используется fallback
* 1 активное → применяется
* 1 активного → ошибка конфигурации

Допустимые значения metric (v1):
 * conversion_rate
 * api_cancel_threshold
 * wallet_threshold

Расширение списка → новая версия контракта.

#### 3.4 Лист wallet_limits

_Назначение: лимиты сумм для wallet-анализатора._

| column      | type                         | required |
| ----------- | ---------------------------- | -------- |
| id          | string                       | yes      |
| enabled     | int (0/1)                    | yes      |
| scope       | string (`partner` / `group`) | yes      |
| scope_value | string                       | yes      |
| limit_type  | string                       | yes      |
| limit_value | number                       | yes      |
| reason      | string                       | yes      |

_Допустимые значения:_
 * limit_type: daily_max_amount
 * scope: partner, group

_Ключ уникальности:_
(scope, scope_value, limit_type)

_Для каждого ключа:_
 * ровно одно активное правило
 * иначе → ошибка конфигурации

### 4. Приоритет и fallback

#### 4.1 Thresholds:  

* (analyzer, partner, metric)  
* (analyzer, default, metric)  
* отсутствие правила → используется логика анализатора

#### 4.2 Wallet limits:

* scope=partner
* scope=group
* отсутствие лимита → лимит не применяется

### 5. Валидации (обязательные):

_Перед применением правил система обязана проверить:_
 * наличие всех обязательных колонок
 * корректность типов (даты/числа)
 * уникальность активных правил по ключам
 * допустимые значения metric, scope, limit_type

_При любой ошибке:_
 * правила не применяются
 * расчёты не запускаются
 * пишется событие config_validation_failed

### 6. Версионирование и воспроизводимость:

 * rules_version — хэш содержимого rules.xlsx
 * contract_version — версия контракта (из meta)

_Каждый job сохраняет snapshot:_
 * rules_version
 * contract_version

_Несовпадение contract_version → отказ запуска._

### 7. Изменение правил:

 * Прямое редактирование Excel не рекомендуется
 * Операционные изменения выполняются через Telegram-бот  


 _* Бот обязан:_
   * управлять enabled
   * предотвращать конфликты
   * писать audit-events


### 8.Гарантии контракта:

_Если контракт соблюдён, система гарантирует:_
   * отсутствие неявных приоритетов
   * отсутствие “тихих” перекрытий
   * объяснимость любого результата расчёта

