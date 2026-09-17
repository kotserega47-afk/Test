# Миграция — этапы, cutover, проверки, первый code PR

| Мета | Значение |
|------|----------|
| **Статус** | PROPOSED |
| **Survey** | [MODULAR_REORG_SURVEY.md](MODULAR_REORG_SURVEY.md) |
| **ADR** | [MODULAR_REORG_ADR.md](MODULAR_REORG_ADR.md) |
| **Этот PR** | только документация (этап 0 / этап 1 начало) |

Merge/deploy рабочих сервисов **не** входят в TASK-2026-09-17-01.

---

## Pin версии на сервис

Один git-репозиторий **не** означает один деплой.

| Сервис | Как закреплять |
|--------|----------------|
| Antares (ныне Test) | Railway: Repo = Test, Branch **или** explicit commit SHA; Start = профиль `antares` |
| Raccoon (ныне Platform) | Пока: Platform `develop` SHA. После переноса: тот же Test repo, **другой** commit pin + `PROJECT_PROFILE=raccoon` |
| WR | отдельный сервис, не деплоить, пока WR gate открыт |

Railway не должен «watch same branch» на трёх сервисах без ручного pin. Предпочтительно: **deploy по SHA**, не «latest branch».

Откат сервиса = вернуть предыдущий SHA **этого** сервиса; не откатывать соседние.

---

## Этап 0 — этот Draft PR (документация)

| | |
|--|--|
| Файлы | `project_memory/ops/MODULAR_REORG_*.md`, Task/Impact/CP, указатели KB |
| Сервисы | **не** затрагиваются |
| Совместимость | 100% |
| Приёмка | review фактов SHA; нет секретов; нет runtime diff |
| Выпуск | merge docs only |
| Стоп | если SHA/prod источник опровергнут ops (поправить docs, не код) |
| Откат | revert docs commit |

---

## Этап 1 — каркас без переключения production

**Task:** [TASK-2026-09-17-02](../active_tasks/TASK-2026-09-17-02_project_profile_skeleton.md)

| | |
|--|--|
| Файлы | `core/project_profile.py`, тесты; **пустые** `modules/{antares,raccoon,wr}/__init__.py` **не** импортируемые из `scheduler.py`; опционально лог профиля |
| Сервисы | при деплое без новой env — поведение jobs **идентично** (смешанный Test процесс остаётся) |
| Совместимость | unset `PROJECT_PROFILE` → `antares` **только как alias для логов**, без вырезания Raccoon jobs |
| Приёмка | pytest новых тестов; `import core.project_profile` без env raise; unknown profile raise; scheduler **не** меняет JOB_REGISTRY |
| Выпуск | pin SHA только если ops хочет; можно не деплоить |
| Стоп | любой diff в `JOB_REGISTRY` keys или handlers |
| Откат | revert commit; данные не менялись |

---

## Этап 2 — характеристика и фиксация поведения (ещё docs + golden)

| | |
|--|--|
| Файлы | golden отчётов (уже есть часть `tests/fixtures`); inventory Platform vs Test raccoon **поведенческий**; зафиксировать команды/schedule contract |
| Сервисы | нет |
| Приёмка | список «must-match» строк отчётов Raccoon с Platform develop; список WE инвариантов с тестами `tests/unit/test_wallet_editor_terminal_field.py` |
| Стоп | нет golden на критичный отчёт |
| Откат | n/a |

Параллельное сравнение версий: **сохранённые xlsx** + send mocked; слово dry-run **не** доказательство. Отдельный флаг `EXTERNAL_SIDE_EFFECTS=0` должен глотать Playwright и TG **явными** stubs, иначе считать побочные эффекты возможными.

---

## Этап 3 — выделить Antares, совместимые entry

| | |
|--|--|
| Файлы | вынос import-side-effects из `downloader.py` / `payout.py`; регистрация jobs через `modules/antares/register.py`; `scheduler` вызывает register только для выбранного профиля **за флагом** `PROJECT_PROFILE_ENFORCE=0` default |
| Сервисы | Antares: опционально; Raccoon Platform: **не** трогать |
| Совместимость | default enforce off = текущий смешанный процесс |
| Приёмка | Antares команды и schedules как сейчас при enforce off; import `analyzers.payout` без chat env (lazy) |
| Выпуск | Antares SHA независимо |
| Стоп | падение hourly/download/WE на staging или первый час prod |
| Откат | предыдущий SHA Antares; WE: не replay неизвестных Save; очередь `{STATE_DIR}/wallet_editor/outbox` сохранить |

---

## Этап 4 — перенос Raccoon (поведение Platform, код в Test repo)

Порядок **после** Antares isolation: иначе один репо продолжит двойной Raccoon.

| | |
|--|--|
| Файлы | порт `job_runner` semantics Platform **или** адаптация PID locks с **другими** job keys; YAML vs Rules — **не** молча подменять источник; TG команды analyzer/reporter |
| Сервисы | новый Raccoon service на Test SHA **или** смена connected repo у существующего |
| Совместимость Platform | старый процесс остаётся, пока новый не прошёл soak |
| Приёмка | те же интервалы (10 min / :00 / daily); fingerprint skip-send; conversion alert dedup (Platform HEAD); **нет** Antares jobs в процессе; token только Raccoon |
| Выпуск | cutover § ниже |
| Стоп | расхождение отчёта; dual polling; двойные выгрузки |
| Откат | вернуть Platform `develop` SHA на Raccoon service; незавершённый download: не считать fail=need retry без сверки файла на диске/кабинете |

**Не** копировать Test `integrations/raccoon_*` как prod Raccoon без diff-review с Platform.

---

## Этап 5 — WR

Только после WR gate (конец документа). Отдельный профиль, отдельные auth/locks/DB. Нет shared Save retry с Antares.

---

## Этап 6 — удаление старых путей

| Удалить | Условие |
|---------|---------|
| Raccoon jobs из процесса Antares | N дней без включения + enforce on |
| Репозиторий Platform как deploy source | Raccoon service на Test SHA soak |
| Legacy `/tmp/auth_state_raccoon.json` | миграция файлов в profile dir выполнена |
| Tracked `.env` на Platform develop | не ждать этапа 6: **отдельный security PR** на Platform (не этот) |

---

## Cutover процесса (старый → новый)

Цель: нет двух исполнителей одного job и двух polling на один token.

### Подготовка

1. Зафиксировать SHA old/new, `PROJECT_PROFILE`, token **presence** (не значение), `STATE_DIR`.
2. Включить на новом: приём jobs **выключен** (`JOB_ACCEPT=0` — реализовать на этапе 3/4, не раньше).
3. Сверить часы MSK.

### Дренаж старого

1. Старый: `JOB_ACCEPT=0` (schedule_loop пропускает dispatch; TG run_* отвечает «drain»).
2. Дождаться `_RUNNING` empty + WalletEditor queues empty (`/status`, registry health).
3. **Не** повторять автоматически операции с **неизвестным** результатом Save: сверка карточки в кабинете / registry, затем ручное решение.
4. Сохранить: outbox files, lock files (не удалять), `next_every`/`next_cron` — в Test они **in-memory**: после рестарта первый cron tick пересчитает `_next_cron_run` (возможна задержка до следующего слота, не дубль того же minute если job ещё lock). Зафиксировать в ops: **пауза до следующего cron/interval**.
5. Остановить polling старого (scale to 0 / stop).

### Пауза (заранее)

| Длительность | Оценка | Влияние | Входящие задания |
|--------------|--------|---------|------------------|
| Telegram polling gap | секунды–минуты между stop old и start new | команды не принимаются | Telegram retries у пользователя; WE Excel **не** копить на старом |
| Schedule | до следующего cron/interval слота | один пропуск hourly если cutover внутри окна | не «догонять» missed download без сверки |
| Dual poll | **0 допустимо** | 409 getUpdates | стоп-условие cutover |

### Старт нового

1. New polling on **тот же** token только после смерти old process (подтвердить отсутствие getUpdates conflict в логах).
2. `JOB_ACCEPT=1`.
3. Наблюдать один полный цикл каждого job + WE smoke на **непрод** карте если политика позволяет; иначе только plan/dry с mocked adapter.

### Запрет dual-run

Locks: разные `STATE_DIR` на время параллельного soak (new в shadow) **или** один volume только после stop old. Shadow = отдельный token + `EXTERNAL_SIDE_EFFECTS=0`.

---

## План проверки (QA)

Отделить **уже красные** тесты baseline (`pytest` на SHA `535994c` записать в этапе 2) от новых падений.

| Проверка | Как | Не является успехом |
|----------|-----|---------------------|
| Формат отчётов Antares | golden `tests/fixtures/hourly`, conversion | «CI зелёный» при отсутствии CI |
| Формат Raccoon | сравнить текст/fingerprint rules с Platform develop fixtures + unit `test_raccoon_*` | совпадение имён файлов |
| Команды | таблица handlers vs `/help` | |
| Schedules | rules snapshot vs hardcoded P list | |
| Изоляция auth | разные storage_state paths; нет чтения чужого файла | |
| Нет чужих jobs | `list(JOB_REGISTRY)` при профиле | |
| Нет дублей cutover | event_log job_started unique per window | |
| WE защиты | `tests/unit/test_wallet_editor_terminal_field.py` и связанные | |
| Незавершённый Save | ручной сценарий unknown → no auto retry | |
| Import hygiene | импорт register-модуля без playwright subprocess | |

---

## Вопросы, без которых нельзя подключать WR

1. Какой **URL** кабинета и контура (login / wallet / payin)?
2. Это тот же продукт, что Antares, или другой DOM?
3. Какие **функции** нужны в v1: только отчёты, только WE, оба?
4. Есть ли Wallet Editor / Auto-Enable / add_wallet у WR ops?
5. Отдельные **credentials**, Telegram bot, Dropbox, PostgreSQL?
6. Совпадают ли поля формы (терминал, chips, статусы) с Antares **на live DOM**?
7. Какие отчёты и расписания обязательны в первый релиз?
8. Кто pin'ит Railway service и какой start command?
9. Политика Save unknown: кто сверяет карточку?
10. Запрет использовать Antares `WALLET_URL` «на всякий случай» — подтверждён?

Пока U4 открыт, `modules/wr` = заглушка, профиль `wr` → «not implemented».

---

## Первая задача с кодом

См. [TASK-2026-09-17-02](../active_tasks/TASK-2026-09-17-02_project_profile_skeleton.md): резолвер профиля + тесты + пустые пакеты **без** смены production JOB_REGISTRY.
