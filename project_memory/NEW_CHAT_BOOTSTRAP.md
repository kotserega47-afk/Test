# NEW_CHAT_BOOTSTRAP — `analizis`

| Мета | Значение |
|------|----------|
| **KB версия** | v1.1 |
| **Последнее обновление** | 2026-05-24 |
| **Назначение** | Быстрый вход в контекст проекта для нового GPT-чата без чтения всего репозитория |
| **Проект** | `analizis` (платёжная операционная аналитика) |

**Первое действие в новом чате:** прочитать этот файл целиком, затем следовать §3 Required Reading Order (только нужные секции под задачу).

---

## 1. Project Summary

### Что делает проект

`analizis` — платформа автоматизации операционной аналитики платежей:

- выгрузка Excel из веб-кабинетов (**Raccoon**, **Antares**) через Playwright;
- анализ conversion / payout / wallet / hourly-метрик;
- доставка отчётов и алертов в **Telegram**;
- бизнес-правила и ACL — из **`rules.xlsx`** в **Dropbox**;
- архитектурные параметры — YAML в `config/`.

**Control vs execution (CONFIRMED):** правила в Dropbox Excel; исполнение в Python; операторы управляют через Telegram-команды.

### Production entrypoint

| Поле | Значение | Статус |
|------|----------|--------|
| **Platform** | Railway | CONFIRMED |
| **Service** | `file-analyzer` | CONFIRMED |
| **Command** | `/opt/venv/bin/python scheduler.py` | CONFIRMED |
| **Config** | `railway.toml`, `nixpacks.toml` | CONFIRMED |

Один long-running процесс: Telegram polling + фоновые Raccoon-циклы + плановый рестарт контейнера (`os._exit(1)`).

### Основные подсистемы

| ID | Подсистема | Prod active? | Статус |
|----|------------|----------------|--------|
| S1 | `scheduler.py` (entry, restart worker) | Yes | CONFIRMED |
| S2 | Telegram polling + commands | Yes | CONFIRMED |
| S3 | Raccoon wallet (hourly :00 MSK) | Yes | CONFIRMED |
| S4 | Raccoon hourly download + report (every ~5 min) | Yes | CONFIRMED |
| S5 | Raccoon daily conversion (00:00–00:02 MSK) | Yes | CONFIRMED |
| S6 | Rules/access (Dropbox → cache → guard) | Yes | CONFIRMED |
| S7 | `telegram_bot` outbound queue | Yes | CONFIRMED |
| — | Antares full `downloader.py` | **Unknown** schedule | UNKNOWN |
| — | Antares hourly pipeline | No | DORMANT |
| — | Bot rules editing (`CONTRACT_BOT`) | No | DOCS_ONLY |

---

## 2. Sources Of Truth

Читать **только** эти KB-файлы как канон после Stage A (не устаревшие разделы `PROJECT_REFERENCE` без сверки).

| Документ | Путь | Содержит |
|----------|------|----------|
| **Architecture Map** | `project_memory/architecture_map.md` | Entrypoints, pipelines P1–P9, integrations, scheduler, persistence, риски U1–U11 |
| **Contracts** | `project_memory/contracts.md` | Runtime/file/env/API/DTO/cache contracts; X-01..X-12 |
| **Current State** | `project_memory/current_state.md` | Снимок prod: active/dormant, env, data files, testing, task drafts |
| **Decisions** | `project_memory/decisions.md` | Почему система устроена так (E1–E17, DOC1–4, dormant) |
| **Risks** | `project_memory/risks.md` | Реестр R01–R32, P0 backlog, critical/architecture/doc/test/ops risks |

**Вне KB (справочно, может быть STALE_RISK):** `ARCHITECTURE.md`, `PROJECT_REFERENCE.md`, `CONTRACT_RULES.md`, `CONTRACT_BOT.md`, `RULES_EDITING.md` — сверять с KB перед выводами.

**Repo code:** источник для **новых** фактов; не пересканировать весь репозиторий, если KB достаточен.

---

## 3. Required Reading Order

### A. Любой новый чат (минимум)

1. **`NEW_CHAT_BOOTSTRAP.md`** (этот файл)  
2. **`current_state.md`** — §1 Executive Summary, §2 Active Runtime, §7 Risk Register (кратко)  
3. **`risks.md`** — §1 Executive Summary, Top 5  

### B. Архитектор / обзор / incident (без TASK)

4. **`architecture_map.md`** — §1 Prod entry, §2 Pipelines (по теме), §6 Scheduler  
5. **`contracts.md`** — §3 Env, §5 Integrations, §8 Cross-contract inconsistencies  
6. **`decisions.md`** — Active Decisions + релевантный Questionable/DOCS_ONLY  

### C. Работа по TASK (обязательно)

1. Bootstrap (A)  
2. **`project_memory/active_tasks/TASK-<id>.md`** — Goal, Scope, Out Of Scope  
3. Точечно KB: секции из Task («Contracts Affected», «Runtime Paths»)  
4. **`contracts.md`** — только перечисленные contract IDs  
5. **`risks.md`** — только перечисленные risk IDs  

### D. Перед предложением изменения кода (GPT → Cursor handoff)

6. **`decisions.md`** — не противоречить E# без нового decision  
7. **`contracts.md`** — breaking change check  
8. Task **Impact** / **review** file, если есть  

**Не читать целиком:** `PROJECT_REFERENCE.md` (9000+ строк), весь `contracts.md` (800+ строк), если Task указывает узкие §.

---

## 4. Active Runtime

Только **CONFIRMED runtime-active** в production entry (`scheduler.py`):

| # | Система | Trigger | Output |
|---|---------|---------|--------|
| 1 | **Telegram polling** | Incoming commands | Replies, manual job dispatch |
| 2 | **ACL / rules guard** | Every command; startup sync | Allow/deny |
| 3 | **Raccoon wallet cycle** | Hourly :00 MSK | PayIn export → wallet analysis → TG |
| 4 | **Raccoon hourly** | Every `RACCOON_HOURLY_EVERY_MIN` (default 5) | `payin.xlsx` → hourly report → TG (dedup) |
| 5 | **Raccoon daily conversion** | 00:00–00:02 MSK | Yesterday summary → TG |
| 6 | **Planned restart** | Fixed MSK times | `os._exit(1)` → Railway restart |
| 7 | **Telegram outbound queue** | Analyzer/downloader send | Messages/files to chats |

**Manual-only (CONFIRMED code, not scheduler loop):** `/run_wallet`, `/run_rate`, `/run_raccoon`, `/run_hourly_raccoon`; Antares `main.py` / `downloader.py` paths.

---

## 5. Critical Contracts

Ссылки на `project_memory/contracts.md` — читать детали там.

### P0 — без этого prod ломается или слепой

| ID | Суть |
|----|------|
| **INT-01** | Telegram: polling + send, два env для токена |
| **RT-010** | `send_message_sync` / `send_file_sync` — `chat_id` обязателен |
| **RT-005** | `get_rules_snapshot()` — Dropbox → `/tmp/rules_cache/` |
| **RT-006** | `AccessRules.get_snapshot()` — листы `access`, `commands` |
| **RT-007** | `check_access()` — fail-closed |
| **FC-001** | `rules.xlsx` schema |
| **INT-02** | Dropbox auth + file ops |
| **SCH-02, SCH-03** | Raccoon scheduled jobs |
| **FC-004, FC-005** | `/tmp/hourly_raccoon/payin.xlsx`, `last_sent.json` |

### Env (имена только — **не значения**)

| Variable | Role |
|----------|------|
| `TG_BOT_TOKEN` | Scheduler polling |
| `TELEGRAM_BOT_TOKEN` | Outbound bot client (import-time) |
| `RULES_XLSX_PATH` | Rules in Dropbox |
| `DROPBOX_*` | File + auth |
| `RACCOON_LOGIN`, `RACCOON_PASSWORD` | Scheduled downloads |
| `TELEGRAM_CHAT_ID_HOURLY_RACCOON`, `TELEGRAM_CHAT_ID_RACCOON_WALLET` | Reports |

### Cross-contract (обязательно помнить)

| ID | Суть |
|----|------|
| **X-01** | `TG_BOT_TOKEN` vs `TELEGRAM_BOT_TOKEN` — STALE_RISK |
| **X-04** | `downloader.py` prod schedule UNKNOWN |

### Decisions (почему так)

| ID | Суть |
|----|------|
| **E2** | `scheduler.py` prod entry |
| **E6** | Raccoon in scheduler threads |
| **E8** | Fail-safe rules / fail-closed ACL |
| **E9** | Restart via `os._exit(1)` |

---

## 6. Critical Risks

Top 5 из `risks.md` (production):

| Rank | ID | Title | Severity |
|------|-----|-------|----------|
| 1 | **R01** | Split `TG_BOT_TOKEN` / `TELEGRAM_BOT_TOKEN` | Critical |
| 2 | **R06** | `scheduler.py` single point of failure | Critical |
| 3 | **R07** | Dropbox + `rules.xlsx` dependency | Critical |
| 4 | **R04** | Secrets in `.env` / git history | Critical |
| 5 | **R02** | `downloader.py` production binding unknown | High |

**Статус-легенда:** CONFIRMED | DORMANT | UNKNOWN | DOCS_ONLY | STALE_RISK

**P0 investigations (risks.md §8):** R01 env audit, R02 downloader binding, R04 secrets check, R07 Dropbox/rules reachability.

---

## 7. Current Open Investigations

Активные TASK-файлы в `project_memory/active_tasks/` (приоритет для нового чата):

| Task | Статус | Цель | Risk |
|------|--------|------|------|
| **[TASK-A3-01-telegram-env-verification.md](active_tasks/TASK-A3-01-telegram-env-verification.md)** | **open** | Подтвердить использование `TG_BOT_TOKEN` и `TELEGRAM_BOT_TOKEN` в коде и Railway | **R01** |

**Правило:** если пользователь не указал TASK — проверить наличие open TASK; для R01 использовать **TASK-A3-01**. Не выполнять remediation в investigation TASK.

**Прочие файлы в `active_tasks/`:** исторические TASK/review/CP от 2026-05-23 — читать только если явно указаны пользователем.

---

## 8. GPT Working Mode

### Роль: **Architect / Reviewer**

| Делать | Не делать (по умолчанию) |
|--------|-------------------------|
| Анализ по KB + Task | Писать/менять `*.py` |
| Уточнять scope, риски, контракты | Рефакторинг «заодно» |
| Draft TASK, impact, review | Менять Railway env / секреты |
| Handoff для Cursor с чётким Scope | Массовый перескан репозитория |
| Помечать CONFIRMED vs UNKNOWN | Выдумывать факты |

### Порядок мышления

1. **Контракты** — что нельзя ломать (`contracts.md`, Task §Contracts).  
2. **Риски** — что может сломать prod (`risks.md`, Task §Risk).  
3. **Решения** — почему уже так (`decisions.md`).  
4. **Текущее состояние** — что реально в prod (`current_state.md`).  
5. **Предложение** — минимальный patch strategy или investigation only.

### Статусы фактов

Всегда различать: **CONFIRMED** | **DORMANT** | **UNKNOWN** | **DOCS_ONLY** | **STALE_RISK**.

### Секреты

- **Никогда** не запрашивать и не записывать в KB значения токенов, паролей, chat IDs с реальными значениями.  
- Только **имена** env из `contracts.md`.

### Если нет TASK

**Stage 0:** предложить draft TASK (как TASK-A3-01), **не код**.

---

## 9. Cursor Working Mode

### Роль: **Implementation Executor**

| Правило | Деталь |
|---------|--------|
| **Вход** | Только через `project_memory/active_tasks/TASK-*.md` (+ optional Context Pack) |
| **Scope** | Строго Task §Scope; §Out Of Scope — запрет |
| **KB** | Не редактировать без явной просьбы |
| **Контракты** | Не нарушать IDs из Task / `contracts.md` |
| **Коммиты** | Только по запросу пользователя |
| **Тесты** | Только если в Task или явно запрошено |

### Handoff от GPT (шаблон)

```text
Task: project_memory/active_tasks/TASK-<id>.md
Role: Cursor Implementation Executor
Read: Task §Goal, §Scope, §Out Of Scope + listed contracts/risks in KB
Do not: expand scope, fix unrelated risks, rotate secrets unless Task says so
```

### Investigation vs implementation

| Тип TASK | Cursor |
|----------|--------|
| Investigation (e.g. TASK-A3-01) | Read-only: grep, Railway UI, logs — **no code/env changes** |
| Implementation | Code change minimal diff |

---

## 10. First Response Template

После загрузки bootstrap новый GPT-чат отвечает **одним блоком** (адаптировать под запрос пользователя):

```markdown
## Bootstrap — analizis

**Прочитано:** NEW_CHAT_BOOTSTRAP.md (+ [перечислить TASK/KB если читали]).

**Проект:** Операционная аналитика платежей — Raccoon scheduled reports, Telegram ops, rules в Dropbox (`rules.xlsx`).

**Prod entry:** Railway → `python scheduler.py` (CONFIRMED).

**Моя роль:** Architect/Reviewer — анализ и TASK/handoff, без кода по умолчанию.

**Активный runtime (кратко):** TG polling + Raccoon wallet/hourly/daily loops + rules ACL + outbound TG queue.

**Top risks:** R01 split TG tokens | R06 scheduler SPOF | R07 Dropbox/rules | R04 secrets | R02 downloader unknown.

**Open investigation:** TASK-A3-01 (R01) — если релевантно запросу.

**Ваш запрос понимаю как:** <одно предложение>.

**Scope этого ответа:** <что сделаю / не сделаю>.

**Out of scope:** изменение кода/env без отдельного implementation TASK.

**Следующий шаг:** <например: открыть TASK-A3-01 / уточнить цель / draft TASK>.
```

### Чеклист перед содержательным ответом

- [ ] Есть ли TASK для запроса?  
- [ ] Затрагивает ли запрос CONFIRMED prod path или DORMANT/DOCS_ONLY?  
- [ ] Какие contract IDs релевантны?  
- [ ] Какие risk IDs (особенно Critical)?  
- [ ] Нужен ли handoff Cursor или только investigation?

---

## Quick links

| Need | Go to |
|------|--------|
| Как устроен prod | `architecture_map.md` §1, §2, §6 |
| Env names | `contracts.md` §3 |
| Что сломано / опасно | `risks.md` §1, §3 |
| Почему так решили | `decisions.md` Active + Questionable |
| Что работает сейчас | `current_state.md` §1–§2 |
| P0 task R01 | `active_tasks/TASK-A3-01-telegram-env-verification.md` |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | Stage KB-BOOTSTRAP: документ создан из KB v1.1 (Stages A1–A5, T1) |
