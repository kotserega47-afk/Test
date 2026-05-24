# TASK-A3-01 — Telegram env verification (R01)

| Мета | Значение |
|------|----------|
| **Task ID** | TASK-A3-01 |
| **Risk ID** | R01 |
| **Статус** | open |
| **Приоритет** | P0 |
| **Тип** | Investigation (read-only) |
| **Создан** | 2026-05-24 |
| **Owner** | — |
| **Связанные KB** | `project_memory/risks.md` (R01), `contracts.md`, `current_state.md`, `decisions.md` (Q1, E5, I3, I4) |

---

## 1. Goal

Подтвердить **фактическое** использование переменных окружения `TG_BOT_TOKEN` и `TELEGRAM_BOT_TOKEN` в:

- локальном коде (кто читает, когда, fail-fast vs optional);
- production на Railway (какие переменные заданы, совпадают ли значения, один ли bot identity для polling и outbound send).

**Не цель задачи:** менять конфигурацию или код — только зафиксировать доказательства для статуса риска R01.

---

## 2. Scope

### Входит

| Область | Что проверить |
|---------|----------------|
| **Imports** | Цепочка import от `scheduler.py` → `tg_commands` → analyzers/downloaders → `telegram_bot` |
| **Startup path** | Порядок инициализации; момент fail-fast при отсутствии каждого env |
| **Scheduler boot** | `scheduler.main()`: `TG_BOT_TOKEN`, `Application.builder().token(...)` |
| **Telegram polling** | Какой токен использует `python-telegram-bot` `run_polling` |
| **Telegram sending** | `telegram_bot.py`: `TELEGRAM_BOT_TOKEN`, `send_message_sync`, `send_file_sync`, `send_photo_sync` |
| **Railway variables** | Наличие обеих переменных в service `file-analyzer`; сравнение значений (без записи секретов в KB) |

### Не входит

- Изменение env в Railway или локально
- Изменение кода (`*.py`)
- Ротация токенов
- Рефакторинг к единому имени переменной
- Настройка CI/CD
- Проверка chat IDs (`TELEGRAM_CHAT_ID_*`) — отдельная задача при необходимости

---

## 3. Why Now

| Поле | Значение |
|------|----------|
| **Risk** | R01 — Split Telegram bot token environment variables |
| **Severity** | **Critical** |
| **Status в KB** | STALE_RISK |
| **Причина срочности** | Polling и outbound используют **разные имена** env; при misconfiguration возможны: crash loop на import, «бот жив» но отчёты не уходят, команды на одном боте / алерты с другого |
| **Backlog** | `risks.md` §8 P0; `current_state.md` TASK-A3-01 draft |

---

## 4. Runtime Paths Affected

| Path | Модули | Роль токена |
|------|--------|-------------|
| **Prod entry** | `scheduler.py` → `main()` | `TG_BOT_TOKEN` — polling |
| **Command routing** | `integrations/tg_commands.py` | Import chain → `telegram_bot` |
| **Outbound reports** | `analyzers/*`, `integrations/*_downloader*`, `bakai_monitor_playwright.py` | `send_message_sync` / `send_file_sync` → `TELEGRAM_BOT_TOKEN` |
| **Transport init** | `integrations/telegram_bot.py` | Import-time: `TELEGRAM_BOT_TOKEN` required |
| **Manual jobs** | `tg_commands._run_job` → executor → sync jobs | Send path via imported modules |

**Схема (для расследования):**

```
scheduler.py (TG_BOT_TOKEN)
    → tg_commands (imports)
        → downloaders / analyzers
            → telegram_bot (TELEGRAM_BOT_TOKEN)  [import-time]
    → app.run_polling()
```

---

## 5. Contracts Affected

Ссылки на `project_memory/contracts.md` (и связанные решения):

| Contract ID | Название | Связь с R01 |
|-------------|----------|-------------|
| **INT-01** | Telegram integration | Auth: `TG_BOT_TOKEN` (polling) vs `TELEGRAM_BOT_TOKEN` (send API) |
| **RT-010** | Telegram outbound (`send_*_sync`) | Требует `chat_id` + working `TELEGRAM_BOT_TOKEN` bot client |
| **RT-011** | Manual TG job runner | Косвенно: jobs вызывают send path |
| **E5** | Telegram as operational interface | `decisions.md` — dual env noted as Q1 |
| **X-01** | Cross-contract: split tokens | Прямое описание риска |
| **§3.1 Env — Telegram** | Таблица env contracts | `TG_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN` required semantics |
| **§3.8 Env conflict summary** | U1 / STALE_RISK | Ожидаемый outcome расследования |
| **I3, I4** | Implicit invariants | `decisions.md` — boot без каждого env |

**Architecture / state:**

- `architecture_map.md` §1.3, U1  
- `current_state.md` §4 startup, §5.1, §5.5 U1  
- `risks.md` R01, R25 (related)

---

## 6. Investigation Plan

Выполнять **только чтение** кода, конфигов деплоя и Railway UI/CLI. Записывать выводы в секцию «Findings» этого TASK (или linked review file) **без значений токенов**.

### Step 1 — Code map (local)

1. `grep`/поиск по репозиторию: `TG_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN`, `getenv`, `BOT_TOKEN`.
2. Для каждого вхождения зафиксировать: файл, строка, required/optional, default, момент ошибки (`RuntimeError` / `ValueError`).
3. Построить import graph от `scheduler.py` до первого `import integrations.telegram_bot` (или `from integrations.telegram_bot import`).
4. Подтвердить: может ли `scheduler` стартовать polling **без** успешного import `telegram_bot`.

**Ожидаемые якоря (CONFIRMED в KB, перепроверить):**

- `scheduler.py` ~138: `TG_BOT_TOKEN`  
- `telegram_bot.py` ~15–18: `TELEGRAM_BOT_TOKEN`, import fail  
- `tg_commands.py`: imports pulling `telegram_bot`

### Step 2 — Startup sequence (local, dry analysis)

1. Прочитать `scheduler.main()` порядок: token check → handlers → `RULES.get_snapshot` → threads → `run_polling`.
2. Определить: при каком минимальном наборе env процесс **не дойдёт** до `run_polling`.
3. Зафиксировать: `load_dotenv()` в `telegram_bot` — влияет ли на local vs Railway.

### Step 3 — Polling vs send identity (local, logic)

1. Документировать: `Application.builder().token(BOT_TOKEN)` vs `Bot(token=TELEGRAM_TOKEN)`.
2. Ответить: это **два клиента** одного API; при разных token values — **два разных бота** (CONFIRMED по дизайну API, не по бизнес-намерению).

### Step 4 — Railway production env (read-only)

1. Открыть Railway project → service `file-analyzer` (имя из `railway.toml`).
2. Проверить Variables:
   - присутствует ли `TG_BOT_TOKEN`;
   - присутствует ли `TELEGRAM_BOT_TOKEN`;
   - **совпадают ли значения** (сравнить локально, в TASK записать только: `MATCH` / `MISMATCH` / `ONLY_ONE_SET` / `BOTH_MISSING`).
3. Проверить deploy start command: `/opt/venv/bin/python scheduler.py` (без изменений).
4. Optional: просмотреть последние deploy logs на предмет `ValueError: Не задан TELEGRAM_BOT_TOKEN` или успешного «Telegram scheduler started».

### Step 5 — Runtime smoke (optional, если разрешено ops)

**Только с согласования владельца бота — не обязательный шаг для закрытия TASK.**

1. Отправить команду `/status` боту (polling path).
2. Триггернуть лёгкий send (например `/run_rate` в окне Bakai или дождаться hourly) — outbound path.
3. Сверить: ответы приходят от **одного** `@username` бота в Telegram.

**Если smoke не выполняется:** TASK может закрыться на code + Railway env evidence.

### Step 6 — Conclusion matrix

Заполнить таблицу в Findings:

| Scenario | R01 status after investigation |
|----------|-------------------------------|
| Both set, values MATCH | Downgrade to **mitigated** / document canonical names |
| Both set, MISMATCH | **CONFIRMED Critical** — remediation task needed |
| Only `TG_BOT_TOKEN` | **CONFIRMED** import/send failure or partial function |
| Only `TELEGRAM_BOT_TOKEN` | **CONFIRMED** scheduler boot failure |
| Neither set | Container not healthy (separate incident) |

### Step 7 — KB update (follow-up, not in this TASK execution)

После утверждения findings — отдельный doc-only PR/task:

- обновить `risks.md` R01 status;
- `contracts.md` X-01;
- `current_state.md` U1;
- при необходимости `decisions.md` Q1 → resolved E#.

---

## 7. Evidence Required

Для закрытия расследования R01 (исследован, не обязательно устранён):

| # | Evidence | Format | Contains secrets? |
|---|----------|--------|-------------------|
| E1 | Таблица всех code references на оба env | Markdown в TASK Findings | No |
| E2 | Import chain diagram или список модулей до `telegram_bot` import | Markdown | No |
| E3 | Railway env checklist: `TG_BOT_TOKEN` present Y/N | Markdown | No values |
| E4 | Railway env checklist: `TELEGRAM_BOT_TOKEN` present Y/N | Markdown | No values |
| E5 | Token parity result: `MATCH` / `MISMATCH` / `ONLY_ONE_SET` | Enum only | **Never paste tokens** |
| E6 | Deploy start command confirmation | Quote from Railway or `railway.toml` | No |
| E7 | Log excerpt: boot success OR import error (redact secrets) | Snippet | Redacted |
| E8 | Optional: smoke test — same `@botusername` for command + report | Screenshot note or text | No token |

**Минимум для закрытия TASK:** E1 + E2 + E3 + E4 + E5 + E6.

---

## 8. Success Criteria

Риск R01 считается **исследованным** (TASK done), когда:

1. **Доказано в коде**, какой модуль читает какой env и при каких условиях падает процесс.
2. **Доказано в Railway**, заданы ли обе переменные и совпадают ли значения (класс E5 без публикации значений).
3. **Зафиксирован итог** по матрице Step 6: `MATCH` | `MISMATCH` | `ONLY_ONE_SET` | etc.
4. **Сформулирована рекомендация** для follow-up (например: «doc-only: canonical env» или «remediation: unify env») — без выполнения remediation в этом TASK.
5. Findings согласованы с `contracts.md` INT-01, RT-010, X-01.

Риск считается **устранённым** (не в scope TASK-A3-01) только после отдельной remediation + повторной проверки E5 = `MATCH` и стабильного smoke.

---

## 9. Out Of Scope

| Запрещено | Причина |
|-----------|---------|
| Изменение `*.py` | Stage T1 / investigation-only |
| Изменение `tests/*` | User rule |
| Изменение Railway env | Explicitly out of scope |
| Ротация или перевыпуск Telegram tokens | Security change — отдельная задача |
| Унификация env в коде | Remediation — отдельный TASK |
| Коммит `.env` или запись секретов в KB/TASK | Security |
| Выполнение расследования в рамках создания этого файла | TASK creation only |
| Исправление R01 | Investigation ≠ fix |

---

## Findings

> Заполняется исполнителем TASK. До выполнения — пусто.

| Field | Value |
|-------|-------|
| **Investigated on** | — |
| **Investigator** | — |
| **Token parity (E5)** | — |
| **Conclusion** | — |
| **Follow-up task** | — |

---

## История

| Дата | Событие |
|------|---------|
| 2026-05-24 | TASK создан (Stage T1) для расследования R01 |
