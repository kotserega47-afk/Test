# Context Pack System v1

| Мета | Значение |
|------|----------|
| **Версия** | 1.0 |
| **KB** | v1.1 |
| **Workflow** | Task Workflow v1 |
| **Статус** | нормативный процесс (не runtime) |

**Назначение:** единый стандарт передачи контекста между **GPT Architect**, **GPT Reviewer**, **Cursor**, **QA Reviewer** и **новым GPT-чатом** без устной истории и без дублирования всей KB.

**Артефакты:**

| Файл | Роль |
|------|------|
| `project_memory/context_pack.md` | спецификация, maintenance, governance (этот документ) |
| `project_memory/templates/context_pack_template.md` | шаблон для заполнения |
| `project_memory/active_tasks/*.md` | источник Active Tasks (не копировать целиком в pack) |
| KB v1.1 (`architecture_map`, `contracts`, …) | источник истины; pack — **индекс и сжатие**, не замена |

---

# 1. Context Pack Specification

## 1.1. Что такое Context Pack

**Context Pack** — ограниченный по объёму снимок знаний, достаточный для старта работы в новом чате/сессии. Pack содержит **ссылки и выжимки** из KB v1.1 и активных task-артефактов, а не полные тексты документов.

**Принципы:**

1. **KB — источник истины;** pack — навигация + критичное для решения.
2. **Только CONFIRMED / явно помеченные риски** в инвариантах и контрактах; DOCS_ONLY не подавать как реализованное.
3. **Один pack — одна цель** (роль + сценарий); универсальный «мега-pack» запрещён governance.
4. **Версионируется** полем `Pack ID` + `Generated` + `Valid until`.

## 1.2. Роли и сценарии

| Роль | Типичный сценарий | Тип pack |
|------|-------------------|----------|
| **GPT Architect** | постановка, impact, обновление KB | `architect` |
| **GPT Reviewer** | архитектурный review по task/PR | `arch_review` |
| **Cursor** | реализация по task `ready` | `dev_task` |
| **QA Reviewer** | приёмка, сценарии, регрессия | `qa_review` |
| **Новый GPT-чат** | онбординг, вопрос без истории | `onboarding` |
| **Incident** | расследование prod-симптома | `incident` |

Шаблон заполнения: `templates/context_pack_template.md`.

---

## 1.3. Обязательные данные (MUST в pack)

Данные, которые **всегда** включаются в любой pack (базовый слой):

| # | Данные | Источник KB | Формат в pack |
|---|--------|-------------|---------------|
| M1 | Идентификация pack | — | Pack ID, Generated, Valid until, Scenario, Role |
| M2 | Project Summary | `architecture_map` § Назначение, `current_state` capabilities R# (кратко) | 5–15 строк |
| M3 | Prod entry & deploy | E# из `decisions`, `current_state` § Конфигурация | 2–5 строк: `<DEPLOYMENT_TARGET>` → `<PRIMARY_ENTRYPOINT>` |
| M4 | Легенда статусов | все файлы KB | таблица CONFIRMED / DORMANT / UNKNOWN / DOCS_ONLY / STALE_RISK |
| M5 | Critical Invariants | `decisions` E# / I# (выжимка) | bullet list, только CONFIRMED |
| M6 | Critical Contracts | `contracts` (высокий риск) | список имён + 1 строка сути |
| M7 | Reading Order | § 1.8 ниже | упорядоченный список файлов KB |
| M8 | Ссылка на полную KB | пути `project_memory/*.md` | без вставки полных файлов |

**Дополнительно MUST по сценарию** — см. § 1.5.

---

## 1.4. Запрещённые данные (MUST NOT в pack)

| # | Запрещено | Причина |
|---|-----------|---------|
| F1 | Полное копирование `architecture_map.md`, `contracts.md` и др. | раздувание; устаревание копии |
| F2 | Содержимое секретов: значения env, токены, пароли | безопасность |
| F3 | DOCS_ONLY как факты runtime (planned features, legacy docs) | ложные ожидания |
| F4 | Предположения о коде без метки CONFIRMED / без ссылки на KB | галлюцинации |
| F5 | Закрытые (`done` / `cancelled`) задачи в Active Tasks | шум |
| F6 | Длинные failure-path таблицы целиком | читать по ссылке в KB |
| F7 | Legacy docs в корне как норма | только через STALE_RISK / DOCS_ONLY |
| F8 | Diff PR, патчи, фрагменты исходного кода | вне scope knowledge workflow |
| F9 | Чаты, голосовые договорённости без фиксации в KB/task | не воспроизводимы |
| F10 | Дублирование impact/review целиком | ссылка на `active_tasks/TASK-*_impact.md` |

**Разрешено:** краткая выжимка (≤3 предложения) из task/impact/review **по теме сценария**.

---

## 1.5. Минимальный объём контекста по сценариям

Базовый слой (M1–M8) **включён всегда**. Ниже — **добавки** (минимум полей pack).

### A. Новая задача (`dev_task` / постановка → `task_template`)

| Секция pack | Минимум | Источник |
|-------------|---------|----------|
| Current State | R#: что active; 1 строка на затронутый контур | `current_state` |
| Active Tasks | только `draft` / `ready` / `in_progress` + ID + Goal 1 строка | `active_tasks/` |
| Critical Invariants | релевантные E# / I# из `decisions` | `decisions` |
| Critical Contracts | high-risk contracts из `contracts.md` | `contracts` |
| Known Risks | STALE_RISK S#; релевантные G# | `tasks`, `current_state` |
| Open Gaps | UNKNOWN по задаче (G# если релевантно) | `tasks` |
| Reading Order | current_state → architecture_map (pipelines) → contracts → decisions → tasks | § 1.8 |
| Ссылка | «Заполнить `task_template.md`» | Workflow |

**Не включать:** полные pipeline tables — только ID затронутых pipelines из `architecture_map.md`.

---

### B. Архитектурный review (`arch_review`)

| Секция pack | Минимум | Источник |
|-------------|---------|----------|
| Active Tasks | связанный TASK-ID + Goal + Affected Pipelines/Modules | task file |
| Critical Invariants | все E/I, затронутые task | `decisions` |
| Critical Contracts | полный список из Affected Contracts задачи | task + `contracts` |
| Known Risks + Open Gaps | G_, S_, STALE_RISK из task и `tasks.md` | task, `tasks` |
| Recent Decisions | E/I, изменённые за последние 30 дней (или с даты task) | `decisions` + task history |
| Reading Order | task → impact (если есть) → architecture_map (pipelines) → contracts → decisions | § 1.8 |
| Ссылка | `review_template.md` (архитектурный фокус) | Workflow |

**Не включать:** Test Coverage детали (это code review).

---

### C. Impact analysis (`architect` + `impact_analysis_template`)

| Секция pack | Минимум | Источник |
|-------------|---------|----------|
| Current State | entry + активные R для затронутых пайплайнов | `current_state` |
| Active Tasks | TASK + Current/Desired Behavior (кратко) | task |
| Critical Invariants | locks, cache, auth, background jobs, config fail-safe (E#) | `decisions`, `architecture_map` |
| Critical Contracts | таблица контрактов из task § Affected Contracts | task, `contracts` |
| Known Risks | G# если модули в scope; STALE_RISK | `tasks` |
| Open Gaps | UNKNOWN G# при platform/config | `tasks` |
| Reading Order | architecture_map (pipelines + background) → contracts → decisions | § 1.8 |
| Ссылка | `impact_analysis_template.md` | Workflow |

**Минимум по pipelines:** для каждого отмеченного `<PX>` — 1 строка happy + 1 строка критичный failure (не полная таблица).

---

### D. Code review (`qa_review` / Cursor review)

| Секция pack | Минимум | Источник |
|-------------|---------|----------|
| Active Tasks | TASK + Success Criteria + Out Of Scope | task |
| Critical Contracts | только затронутые + Breaking? из task/impact | task, impact |
| Known Risks | STALE_RISK, регрессии из impact § Regression | impact |
| Open Gaps | не блокирующие merge, но принятые риски | impact, review draft |
| Reading Order | task → impact → contracts (секции по модулям) → architecture_map (pipelines) | § 1.8 |
| Ссылка | `review_template.md` | Workflow |

**Не включать:** полный Business Context, если не менялся UX отчётов.

---

### E. Incident investigation (`incident`)

| Секция pack | Минимум | Источник |
|-------------|---------|----------|
| Project Summary | 3 строки: что делает prod | `architecture_map` |
| Current State | prod entry только | `current_state` |
| Critical Invariants | error handling in jobs (E#), locks, auth, restart policy | `decisions` |
| Critical Contracts | contracts по домену симптома; lock timeout | `contracts` |
| Known Risks | STALE_RISK; failure path 1 строка для предполагаемого pipeline | `architecture_map`, `tasks` |
| Open Gaps | platform UNKNOWN gaps явно как «не в KB» | `tasks` |
| Active Tasks | только если инцидент связан с текущей задачей | `active_tasks` |
| Reading Order | architecture_map (failure paths) → contracts → tasks (Gaps) | § 1.8 |

**Не включать:** Desired Behavior будущих фич; dormant activation планы.

**Ограничение KB:** полноценный incident runbook может **отсутствовать** — pack фиксирует симптом → гипотеза pipeline → ссылки KB; ops-детали `<DEPLOYMENT_TARGET>` — UNKNOWN до заполнения в KB.

---

### F. Новый GPT-чат / онбординг (`onboarding`)

| Секция pack | Минимум |
|-------------|---------|
| Все M1–M8 | полная выжимка Project Summary + Current State (capabilities R# кратко) |
| Critical Invariants | top-10 E# / I# |
| Critical Contracts | top-8 высокого риска |
| Known Risks | S# + top STALE_RISK |
| Open Gaps | открытые G# одной таблицей |
| Active Tasks | все не-done (ID + Goal) |
| Recent Decisions | последние E# / I# (минимум 5) |
| Reading Order | полный § 1.8 |

---

## 1.6. Секции pack (маппинг на шаблон)

| Секция шаблона | Содержание | Лимит (governance) |
|----------------|------------|-------------------|
| Project Summary | назначение, prod entry, основные контуры R# | ≤ 20 строк |
| Current State | active/dormant/DOCS_ONLY одной таблицей | ≤ 25 строк |
| Active Tasks | ID, статус, Goal 1 строка, ссылка на файл | ≤ 10 задач |
| Critical Invariants | E/I только CONFIRMED | ≤ 15 пунктов |
| Critical Contracts | имя + нарушение = high risk | ≤ 12 пунктов |
| Recent Decisions | дата + ID + 1 строка | ≤ 10 пунктов |
| Known Risks | STALE_RISK, S_, риски из active tasks | ≤ 15 пунктов |
| Open Gaps | G_, UNKNOWN | ≤ 15 пунктов |
| Reading Order | нумерованный список | ≤ 10 шагов |

---

## 1.7. Передача между агентами

| От → К | Действие |
|--------|----------|
| GPT Architect → Cursor | pack `dev_task` + путь к `active_tasks/TASK-*.md` + impact при medium+ |
| GPT Architect → GPT Reviewer | pack `arch_review` + task + impact |
| Cursor → QA Reviewer | pack `qa_review` + ссылка на PR (вне pack) |
| Любой → новый чат | pack по сценарию + «прочитай Reading Order» |
| Incident → Architect | pack `incident` + симптом; после — новый task |

**Сообщение-обёртка (стандарт):**

```text
Context Pack: <Pack ID> | Scenario: <type> | KB v1.1
Valid until: <date>
Заполни работу по Reading Order. KB — источник истины; pack — индекс.
Файл pack: project_memory/active_tasks/<Pack ID>.md (или вставка ниже)
```

---

## 1.8. Reading Order (нормативный для KB v1.1)

| Шаг | Документ | Когда обязателен |
|-----|----------|------------------|
| 1 | `current_state.md` | всегда |
| 2 | `architecture_map.md` | задачи, impact, incident, onboarding |
| 3 | `contracts.md` | задачи с данными/API/env/integrations |
| 4 | `decisions.md` | review, impact, архитектурные изменения |
| 5 | `tasks.md` | риски, gaps, ops one-shot |
| 6 | `active_tasks/TASK-*.md` | если есть связанная задача |
| 7 | `active_tasks/TASK-*_impact.md` | impact / code review medium+ |
| 8 | `templates/*_template.md` | при создании артефакта workflow |

**Не входит в Reading Order:** legacy docs в корне — только при явном STALE_RISK расследовании, с меткой DOCS_ONLY.

---

# 2. Context Pack Maintenance Model

## 2.1. Кто обновляет

| Артефакт | Ответственный | Триггер |
|----------|---------------|---------|
| Секции pack (выжимка) | **Владелец задачи** (инициатор) или **GPT Architect** | создание/смена статуса task |
| Critical Invariants / Contracts в pack | **GPT Architect** | изменение scope, impact вердикт |
| Open Gaps / Known Risks | **GPT Architect** или **GPT Reviewer** | новый G_, закрытие gap в `tasks.md` |
| KB v1.1 (источник истины) | **GPT Architect** после merge, если меняется CONFIRMED | Approve + флаг «обновить KB» в review |
| Pack для onboarding | **Владелец KB** (назначенный в команде) | раз в 14 дней или после KB minor bump |

**Cursor / QA** не редактируют KB; могут предложить gap через комментарий в review → Architect обновляет `tasks.md`.

## 2.2. Когда обновлять

| Событие | Действие |
|---------|----------|
| Новая task `ready` | сгенерировать pack `dev_task` |
| Impact вердикт high / proceed with caution | обновить Known Risks + Open Gaps в pack |
| Task → `done` | убрать из Active Tasks в pack; архивировать pack snapshot |
| Merge с «обновить KB» | обновить KB, затем **пересобрать** pack (не патчить вручную старый) |
| Инцидент закрыт | добавить 1 строку в Known Risks или G_ в `tasks.md`; incident pack → архив |
| Прошло **Valid until** | pack считается устаревшим; не использовать для решений |

## 2.3. Что считается устаревшим

| Признак | Статус |
|---------|--------|
| `Valid until` истёк | **STALE pack** |
| Task в pack `done`, а pack не пересобран | **STALE pack** |
| В pack есть DOCS_ONLY без метки | **INVALID pack** |
| Pack ссылается на gap, закрытый в KB § «Закрыто» | **STALE pack** |
| Расхождение pack ↔ task file (разные pipeline IDs) | **INVALID pack** |

Устаревший pack **нельзя** прикладывать к новому чату; разрешено только Reading Order + свежая KB.

## 2.4. Предотвращение разрастания

1. **Один сценарий — один pack**; запрет universal pack (governance).
2. **Ссылка вместо копии** на KB и task/impact/review файлы.
3. **Лимиты строк** по § 1.6 и Governance § 3.1.
4. **Active Tasks ≤ 10**; остальное — только ID в `tasks.md` / трекере вне repo.
5. **Recent Decisions ≤ 30 дней** или с момента создания task.
6. **Ежеквартальная** ревизия onboarding-pack владельцем KB (удаление дубликатов с KB).

---

# 3. Context Pack Governance Model

## 3.1. Максимальный размер

| Метрика | Лимит | Превышение |
|---------|-------|------------|
| Общий объём pack | **≤ 400 строк** markdown | разбить на pack + task file |
| Project Summary | ≤ 20 строк | |
| Critical Invariants | ≤ 15 пунктов | вынести в decisions, в pack — ссылка |
| Critical Contracts | ≤ 12 пунктов | |
| Active Tasks | ≤ 10 записей | |
| Вставка из KB | ≤ 0 (только цитаты ≤ 2 строки на пункт) | |

**Целевой размер для Cursor `dev_task`:** 120–200 строк (включая таблицы).

## 3.2. Допустимые источники

| Источник | Допустимо |
|----------|-----------|
| `project_memory/architecture_map.md` | да |
| `project_memory/contracts.md` | да |
| `project_memory/current_state.md` | да |
| `project_memory/decisions.md` | да |
| `project_memory/tasks.md` | да |
| `project_memory/active_tasks/*.md` | да (ссылки + краткая выжимка) |
| `project_memory/templates/*` | да (как инструкция процесса) |
| `project_memory/context_pack.md` | да (правила) |
| Task Workflow v1 артефакты | да |

## 3.3. Запрещённые источники

| Источник | Причина |
|----------|---------|
| Исходный код `.py` | вне knowledge workflow |
| Legacy root docs (`<LEGACY_DOC>.md`) | DOCS_ONLY / STALE_RISK S# |
| Git history, PR comments без фиксации в task | не нормативны |
| Личные заметки, чаты | не воспроизводимы |
| Значения секретов / .env | безопасность |
| Старые версии pack без Pack ID | нет трассировки |

## 3.4. Срок актуальности информации

| Тип информации | Valid until (default) | Обновление |
|----------------|----------------------|------------|
| Pack `dev_task` / `incident` | **3 календарных дня** | пересборка при смене task |
| Pack `arch_review` / `qa_review` | **7 дней** | до закрытия review |
| Pack `onboarding` | **14 дней** | владелец KB |
| Critical Invariants / Contracts | совпадает с KB v1.1 | при bump KB |
| Active Tasks | **в реальном времени** | при смене статуса |
| Open Gaps G# | до закрытия в KB | при ops-интервью |

**Правило:** в каждом pack поля `Generated` (дата) и `Valid until` (дата) **обязательны**.

## 3.5. Идентификация и хранение

| Поле | Формат |
|------|--------|
| Pack ID | `CP-<scenario>-<TASK-ID or ONBOARD>-<YYYYMMDD>` |
| Пример | `CP-dev_task-TASK-YYYY-MM-DD-NN-YYYYMMDD` |
| Хранение | `project_memory/active_tasks/<Pack ID>.md` или вложение в сообщение агенту |

**Архив:** done/cancelled task → pack переносится в `project_memory/active_tasks/archive/` (рекомендация; не обязательно в v1.0).

## 3.6. Контроль качества pack

Перед отправкой в новый чат проверить:

- [ ] Сценарий и роль указаны
- [ ] Valid until в будущем
- [ ] Нет F1–F10 (запрещённые данные)
- [ ] DOCS_ONLY нигде не без метки
- [ ] Reading Order соответствует сценарию § 1.5
- [ ] Active Tasks без done/cancelled
- [ ] Объём ≤ 400 строк

---

# 4. Связь с Task Workflow v1

| Workflow артефакт | Context Pack |
|-------------------|--------------|
| `task_template.md` | pack `dev_task` подставляется в начало чата Cursor |
| `impact_analysis_template.md` | pack `architect` + Reading Order шаг 2–4 |
| `review_template.md` | pack `arch_review` или `qa_review` |

**Порядок в сессии:** Context Pack → Reading Order (KB) → Template Workflow → работа.

---

# 5. Версионирование системы

| Версия | Изменения |
|--------|-----------|
| Context Pack v1.0 | первая спецификация (этот документ) |
| KB v1.1 | совместимая база |

При выходе KB v1.2 — пересмотреть § 1.8 Reading Order и Critical Contracts список.

---

## История

| Дата | Автор | Событие |
|------|-------|---------|
| 2026-05-23 | — | Context Pack System v1.0 создан |
