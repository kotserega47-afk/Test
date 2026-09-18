# Миграция — этапы, cutover, проверки, первый code PR

| Мета | Значение |
|------|----------|
| **Статус** | PROPOSED (этап 0–1 в Draft PR; runtime Test ещё не выкатывался) |
| **Survey** | [MODULAR_REORG_SURVEY.md](MODULAR_REORG_SURVEY.md) |
| **ADR** | [MODULAR_REORG_ADR.md](MODULAR_REORG_ADR.md) |

Merge/deploy **намеренно** не входят в TASK-2026-09-17-01/02/03. Это **не** значит, что merge в подключённую ветку безопасен: см. § Автодеплой.

### Состояние программы (2026-09-17)

| Task | Что сделано | Merge/deploy |
|------|-------------|--------------|
| TASK-02 парсер | реализован, review пройден (Draft PR #5) | **нет** |
| TASK-03 early gate | реализован, review пройден (Draft PR #6, HEAD `48a2a82…`) | **нет** |
| TASK-04 эталон | подготовлен, review пройден (Draft PR #7, HEAD `443ba70e…`; docs `2bc0ee0…`) | **нет** |
| TASK-05 Antares jobs | реализован, review пройден (Draft PR #8, HEAD кода `a6d7ebcf…`; docs `0ce4d53…`) | **нет** |
| TASK-06 TG handlers | план подготовлен, review пройден (Draft PR #9, HEAD `380a4bb…`) | **нет** |
| TASK-07 Antares run cmds | реализован, review пройден (Draft PR #10, HEAD `94be3127…`); isolated entry нет | **нет** |
| TASK-08 Antares dispatch | реализован, review пройден (Draft PR #11, HEAD `7169cd48…`); шесть handlers; isolated entry нет | **нет** |
| TASK-09 registry cmds | реализован, review пройден (Draft PR #12, HEAD `b8085a28…`); восемь handlers; isolated entry нет | **нет** |
| TASK-10 registry export | реализован, review пройден (Draft PR #13, HEAD `8c37766…`); девять handlers; isolated entry нет | **нет** |
| TASK-11 Auto-Enable cmds | реализован, review пройден (Draft PR #14, HEAD `0fa283eb…`); 11 handlers; isolated entry нет | **нет** |
| TASK-12 document ingest plan | план принят, review пройден (Draft PR #15, закрытие `66b99ab1…`); runtime плана не менялся | **нет** |
| TASK-13 document ingest | код перенесён в `modules.antares.document_ingest`; review **не** пройден | **нет** |
| Модули проектов | пакеты `modules/*`; 11 callbacks в `modules.antares.handlers`; ingest owner `modules.antares.document_ingest`, mixed re-export `integrations.wallet_editor_tg` | — |
| Рабочий режим | **legacy mixed** (unset / пустой / whitespace `PROJECT_PROFILE`) | prod без этих PR |
| Явные профили | `antares` / `raccoon` / `wr` на mixed entry **отклоняются** (после выката TASK-03) | не в prod |
| Следующая | isolated entry **не** готов; mixed help/status/Raccoon/`run_script_hello` ещё в `tg_commands`; document ingest owner перенесён (TASK-13, review не пройден) | — |

Проверка тестов TASK-02: GPT — **18** тестов на `acfb9958…` в изолированной директории (полный набор проекта не запускался). TASK-02/03 вместе: **41 passed**, Python **3.13.14**, прогон **Cursor**. GPT смотрел diff PR #6, набор 41 **не** перезапускал.

Draft PR #4 / #5 / #6 / #7 / #8 / #9 / #10 / #11 / #12 / #13 / #14 / #15 / #16 **пока не сливать**.

Перед выпуском отдельно: автодеплой Test; активные задания; **нет непустого `PROJECT_PROFILE`** у сервиса Test (иначе после TASK-03 процесс не стартует).

После согласования выпуска: слить **#4** → переназначить base **#5** и проверить diff → слить **#5** → переназначить base **#6** и проверить diff. Если после переназначения код изменился, прежний review **не** считать автоматически действующим.

Предлагаемые флаги **`JOB_ACCEPT` и `EXTERNAL_SIDE_EFFECTS` в коде отсутствуют.** Ниже они — требования будущих PR, не текущий runtime.

---

## Автодеплой и независимый выпуск

### Что уже подтверждено (Railway connector, 2026-09-17)

Два **разных** сервиса, разные git-источники:

| Сервис | Connected branch | Deployed SHA | Следствие при merge |
|--------|------------------|--------------|---------------------|
| Test (`542c84d6-…`) | Test `test_main` | `535994c…` | merge в `test_main` **может** собрать и **перезапустить** этот сервис |
| Raccoon (`0060b136-…`) | Platform `develop` | `ebbcd6c…` | merge в Test `test_main` **сам по себе** не двигает этот SHA; merge в Platform `develop` может перезапустить Raccoon |

`list_deployments` status `SUCCESS` = сборка/выкат платформы. **Не** job health, **не** отсутствие двойного Raccoon.

### Чего нельзя считать настроенным

Произвольный «pin SHA» в Railway **не подтверждён** как включённая возможность этих сервисов. Пока дашборд не показывает явную фиксацию commit (и это не меняется в этой задаче), исходить из модели: **сервис следит за веткой** и выкатывает новый HEAD после push/merge.

Production-настройки Railway в этой задаче **не менять**.

### Проверяемый способ независимого выпуска (без смены текущих настроек)

Сегодня независимость уже есть **на уровне разных репозиториев/веток**:

1. Выпуск Antares/Test: менять только `deniskotdavydov1991-wq/Test` / `test_main`. После merge проверить, что Raccoon deployment SHA остался `ebbcd6c…` (или актуальный зафиксированный), а Test получил новый SHA.
2. Выпуск Raccoon: менять только `Platform_2.0` / `develop`. Проверить, что Test deployment SHA не сдвинулся.
3. Docs/code в Test, который **нельзя** рестартовать: не merge в `test_main`, пока не согласован restart (оставить Draft; либо дождаться окна). Merge документации **может** вызвать тот же Nixpacks+Playwright restart, что и code.

Когда оба сервиса окажутся на одном репозитории, независимость **нужно будет заново доказать** одним из проверяемых вариантов (настройки тогда — отдельный ops-PR, не эта задача):

| Вариант | Как проверить |
|---------|----------------|
| Две release-ветки | сервис A connected `release/antares`, сервис B `release/raccoon`; merge в одну ветку не меняет SHA другого |
| Два репозитория как сейчас | connected repo различается |
| Явный commit in dashboard | скрин/API: поле commit зафиксировано и auto-deploy from branch **off** |

Не описывать pin SHA как уже доступный рычаг.

Откат сервиса = выкат **предыдущего известного SHA той же ветки/репо этого сервиса** (revert merge или redeploy старого deployment id). Соседний сервис не откатывать «заодно».

---

## Этап 0 — этот Draft PR (документация)

| | |
|--|--|
| Файлы | `project_memory/ops/MODULAR_REORG_*.md`, Task/Impact/CP, указатели KB |
| Runtime-код | **не** меняется |
| Сервисы | код jobs тот же; **merge в `test_main` может перезапустить Test** (автодеплой). Raccoon при merge только в Test — SHA не должен сдвинуться |
| Совместимость поведения после рестарта | ожидается та же, **если** выкатится тот же runtime; рестарт сам по себе рвёт in-memory schedules/polling |
| Приёмка | review фактов; нет секретов; git diff без `.py` runtime |
| Выпуск | merge только с явным окном на возможный restart Test **или** без merge, пока Draft |
| Стоп | несогласованный автодеплой; опровергнутые SHA |
| Откат | revert docs commit на `test_main` (это тоже деплой) |

---

## Этап 1 — каркас без переключения поведения jobs

**Task:** [TASK-2026-09-17-02](../active_tasks/TASK-2026-09-17-02_project_profile_skeleton.md) — **реализован, review пройден, не слит.**

Только: чистый парсер + пустые пакеты + unit-тесты. **Нет** импорта из `scheduler.py` / `tg_commands.py`.

Текущий процесс Test остаётся **смешанным** (Antares + зарегистрированные Raccoon jobs в коде). Это **не** изолированный профиль `antares`.

Профили `raccoon` / `wr` **нельзя** подавать в legacy `scheduler.py`: он регистрирует общий набор jobs независимо от строки профиля.

| | |
|--|--|
| Файлы | `core/project_profile.py`, `tests/unit/test_project_profile.py`, пустые `modules/{antares,raccoon,wr}/__init__.py` |
| Сервисы | при merge в `test_main` — риск **рестарта Test**; состав jobs не должен измениться |
| Приёмка | pytest парсера; diff без `scheduler.py` |
| Стоп | любой hook в entrypoints; любой diff `JOB_REGISTRY` / handlers |
| Откат | revert; данных нет |

Ранняя проверка профиля в процессе: [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **реализована, review пройден, не слита.**

---

## Этап 2 — характеристика и фиксация поведения

**Task:** [TASK-2026-09-17-04](../active_tasks/TASK-2026-09-17-04_behavior_baseline.md) — **review пройден**, Draft PR #7, merge нет. Артефакт: [MODULAR_REORG_BEHAVIOR_BASELINE.md](MODULAR_REORG_BEHAVIOR_BASELINE.md).

| | |
|--|--|
| Файлы | golden; поведенческий diff Raccoon Platform vs Test |
| Сравнение версий | только на сохранённых входах **или** среда без внешних изменений. Нужны **явные stubs** Playwright/TG |
| `EXTERNAL_SIDE_EFFECTS` | **предложение, кода нет.** Пока флага нет, слово dry-run **не** доказывает отсутствие побочных эффектов |

---

## Этап 3 — выделить Antares, совместимые entry

Регистрация jobs через модуль; mixed legacy остаётся default, пока enforce не включён.  
`JOB_ACCEPT` / drain — **предложение, кода нет**; реализовать **до** cutover, не в TASK-02.

**Сейчас (после TASK-13):** owner ingest — `modules.antares.document_ingest`; mixed `wallet_editor_tg` — identity re-export. Review TASK-13 **не** пройден. Isolated Antares **не** готов.

Совместимость: enforce off = **смешанный** процесс, не «уже antares-only».

Выпуск: только сервис Test (`test_main`), проверить неизменность Raccoon SHA.

---

## Этап 4 — перенос Raccoon

Порт **поведения Platform `develop`**, не copy-paste Test `integrations/raccoon_*`.

**Soak до переключения** = сравнение **без** внешних эффектов (отдельный токен или stubs; **не** второй исполнитель на том же токене/тех же кабинетах).

После переключения = **один** активный исполнитель. Старый SHA **сохранён для отката**, процесс **остановлен и не принимает задания**.

---

## Этап 5–6

Без изменений смысла: WR после gate; удаление старых путей после подтверждённого перехода. Tracked `.env` на Platform develop — отдельный security PR.

---

## Рычаги

| Рычаг | Статус | Зачем | Где появиться |
|-------|--------|-------|----------------|
| `parse_project_profile` | код в Draft PR #5 | разбор строки | TASK-2026-09-17-02 (review; не merged) |
| Early profile gate | код в Draft PR #6 | проверка **до** import с env/I/O/JOB_REGISTRY | TASK-2026-09-17-03 (review; не merged) |
| `JOB_ACCEPT` | нет | прекратить приём новых jobs, не убивая процесс | до cutover (этап 3/4) |
| Durable queue + schedule cursor | нет / частично | TG updates и cron не только in-memory | до cutover |
| `EXTERNAL_SIDE_EFFECTS=0` | нет | shadow-сравнение | этап 2/4 сравнение |
| Isolated entry per profile | нет | `raccoon`/`wr` не на legacy mixed scheduler | после gate + register |

---

## Cutover (старый → новый исполнитель)

Цель: нет двух исполнителей одного job и двух polling на один token.

### Роли версий

| Фаза | Старый | Новый |
|------|--------|-------|
| До переключения | единственный исполнитель | не polling / не jobs на prod token; сравнение без внешних эффектов |
| Переключение | drain → stop → **не** auto-restart | единственный исполнитель |
| После | образ/SHA хранится для отката, **0 реплик**, не выполняет задания | работает |
| Откат | сначала **стоп нового** (как ниже), затем старт старого SHA как единственного | остановлен |

### 1. Прекращение приёма новых заданий

Нужен будущий `JOB_ACCEPT=0` (или эквивалент) на **старом**:

- schedule_loop не делает **новый** `dispatch`;
- TG `/run_*`, **новые** document ingest, Auto-Enable, script_jobs — отказ «не принимаем», **без постановки новых** задач в worker;
- внутренние bridge — не enqueue **новых** карточек.

`JOB_ACCEPT=0` **не** останавливает воркеры, уже разбирающие принятые задачи, и **не** должен делать очередь вечно заблокированной.

Пока рычага нет, «просто остановить контейнер» рвёт принятые, но не дописанные операции — неприемлемо как штатный cutover.

### 2. Уже принятые задания (drain ≠ freeze)

Запрет **новых** заданий не должен мешать **завершению уже принятых**.

Для каждой принятой, но не законченной единицы работы — одно из двух:

- **дожать** в этом процессе (worker продолжает; Save/download до конца или до известного fail);
- **сохранить durable** для передачи новому исполнителю (outbox / inbox с ключом операции), затем не исполнять повторно здесь.

**Недопустимо:** `JOB_ACCEPT=0` при котором принятые задачи не обрабатываются и не сериализуются, а cutover ждёт «пустую очередь» — это взаимная блокировка.

Timeout дожима → эскалация ops, не silent kill посередине Save.

Индикаторы idle **после** дожима или persist: `_RUNNING` пуст, WE queues пусты **или** все remaining записи в durable handoff.

### 3. Durable-сохранение оставшейся очереди

Сохранить и **не чистить**:

- `{STATE_DIR}/wallet_editor/outbox` и `results/`;
- registry PG rows;
- fingerprint/dedup файлы (`last_sent.json`, conversion alert state) — скопировать в согласованный каталог, если путь `/tmp` (сейчас часть состояния **не** durable).

In-memory `next_every` / `next_cron` в Test **пропадут** при stop. До cutover нужен будущий **schedule cursor** (last fired slot per job_type) в `STATE_DIR`, иначе слоты только угадываются.

### 4. Telegram updates и защита от повторной обработки

Это **будущий** контракт (не TASK-02). Сейчас offset getUpdates в процессе **не** durable.

Различать:

| Событие | Как трактовать | Ключ (предложение) |
|---------|----------------|-------------------|
| Повторная доставка того же Telegram **update** (retry / dual poll / restart) | не создавать вторую операцию | `(bot_id, project_profile, update_id)` |
| Пользователь **намеренно** прислал тот же Excel ещё раз | **новый** запрос | новый `update_id`; не ключ `file_id` / `file_unique_id` |
| Тот же файл в другом проекте / другом боте | другая область | `project` и `bot` входят в уникальность |

`file_id` / `file_unique_id` **сами по себе не** ключ операции: один файл можно отправить повторно сознательно.

Подтверждение получения оператору (`reply` / «принято») согласовать с durable-сохранением: не обещать «в очереди», пока запись inbox/outbox не зафиксирована; после фиксации — можно stop polling.

Входящие updates **не** оставлять на «пользователь отправит ещё раз» как основной план. Без inbox cutover только в окне с объявленной потерей *ещё не принятых* команд.

Dual polling на один token **запрещён**. Нет getUpdates conflict ≠ старый процесс остановлен.

### 5. Слоты расписания: выполненные и пропущенные

По cursor (когда появится):

- слот **выполнен** → новый не дублирует;
- слот **пропущен** во время паузы → не «догонять» download/Save без сверки кабинета/файла;
- слот **неизвестен** → как пропущенный, не как failed-retry.

Пока cursor нет: заранее объявить паузу до следующего wall-clock слота; не replay missed jobs автоматически.

### 6. Неизвестный результат Save

Операция, где Save мог пройти, а ответ не получен:

1. не ставить в очередь как «невыполненную»;
2. сверка карточки в кабинете + registry;
3. ручное решение ops (закрыть / дописать статус / повтор **осознанный**).

Автоповтор запрещён.

### Пауза

Между stop old polling и start new polling: входящие updates зависят от Telegram retry **и** от будущего inbox. Основной механизм сохранения — **durable запись до stop**, не просьба к оператору переслать Excel.

Длительность: заранее в окне (минуты). Влияние: нет новых исполнений; расписание — пропуск слотов по правилам § 5.

---

## Блокировки, стоп, передача права, откат

### Предел текущих locks

PID-файлы `{STATE_DIR}/locks/*.lock` и in-memory locks Platform **не координируют два контейнера**. Разный `STATE_DIR` тоже не стопает чужой процесс. Это single-process single-flight, не cluster lock.

### Подтверждение остановки старого исполнителя

Нужны **несколько** независимых сигналов (все, что доступны):

1. Railway: replicas = 0 / service stopped **этого** service id; deployment не в состоянии активного restart loop.
2. Нет живого процесса с прежним PID на инстансе (если есть exec/logs).
3. Нет новых `job_started` / WE worker логов с **этого** service после T_stop.
4. Telegram: новый процесс ещё **не** запущен; отсутствие conflict — лишь вспомогательный, недостаточный признак.

### Предотвращение повторного запуска старого (anti-restart)

Отключить auto-deploy **само по себе недостаточно**:

- уже запущенный контейнер **продолжает** работать;
- restart policy / crash loop / «restart service» в Railway может поднять процесс снова на том же SHA.

Нужен **проверенный** комплект (зафиксировать в чеклисте окна cutover, настройки менять только тогда):

1. **Остановка** исполнителя: Stop / replicas=0 / удаление активного deployment **этого** service UUID — подтвердить по статусу сервиса и отсутствию логов `job_started` после T_stop.
2. **Удержание остановленным:** auto-deploy off **и** restart/replicas не возвращают контейнер (проверить, что после stop нет нового ACTIVE replica в течение окна наблюдения).
3. Не держать второй сервис с тем же Telegram token в ACTIVE.

Пока механизм не проверен на этих сервисах — считать anti-restart **не доказанным**. PID lock и `STATE_DIR` его не заменяют.

### Передача права выполнять задания

Явный акт, не «оба живы, новый с JOB_ACCEPT=1»:

1. Старый: drain (`JOB_ACCEPT=0`, когда появится) → idle → stop + anti-restart.
2. Проверка стопа (§ выше).
3. Новый: единственный ACTIVE на токене; затем `JOB_ACCEPT=1`.
4. Запись: old deployment id, new deployment id, время передачи.

Пока `JOB_ACCEPT` нет, шаг 1 заменяется согласованным stop только после idle (хуже; не штатный путь после появления флага).

### Откат

1. **Сначала** остановить новый (drain если успел принять работу → idle → stop + anti-restart нового).
2. Не запускать старый, пока новый ещё ACTIVE.
3. Сохранить очередь, outbox, dedup state **как лежат**; не чистить lock files «для порядка».
4. Совместимость состояния со старой версией: checklist **до** cutover (какие файлы/колонки новая версия добавила). Если новая писала нечитаемый формат — откат кода без миграции данных **запрещён**, нужен отдельный rollback данных.
5. Старт старого SHA как единственного исполнителя.
6. Неизвестные Save, начатые новым — сверка, не auto-replay.

---

## План проверки (QA)

Отделить уже красные тесты на SHA `535994c` (этап 2) от новых падений. Отсутствие CI ≠ успех.

| Проверка | Как | Не является успехом |
|----------|-----|---------------------|
| Формат отчётов Antares | golden | «деплой SUCCESS» |
| Формат Raccoon | vs Platform develop | наличие `raccoon_*` в Test репо |
| Команды / schedules | handlers vs `/help`; rules vs hardcoded P | |
| Изоляция | разные auth paths | PID lock на одном контейнере |
| Нет чужих jobs | `JOB_REGISTRY` **после** isolated entry (не на legacy mixed) | |
| Нет дублей cutover | cursor + event_log | нет getUpdates conflict |
| WE защиты | unit terminal field | |
| Незавершённый Save | сценарий unknown → no auto retry | |
| Независимый выпуск | после merge Test SHA сдвинулся, Raccoon SHA нет (и наоборот) | |

---

## Вопросы, без которых нельзя подключать WR

Пока U4 открыт: парсер может принять литерал `wr`; **процесс** не регистрирует jobs (TASK-03 отклоняет, пока нет isolated WR entry).

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

---

## Следующие задачи (не merge #4–#16)

1. Isolated entry, `JOB_ACCEPT` и cutover **не** готовы. Help/status/Raccoon/`run_script_hello` остаются в mixed `tg_commands`. Document ingest: план в `ops/MODULAR_REORG_DOCUMENT_INGEST.md`, код не перенесён.
