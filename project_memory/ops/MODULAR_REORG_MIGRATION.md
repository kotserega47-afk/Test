# Миграция — этапы, cutover, проверки, первый переход Antares

| Мета | Значение |
|------|----------|
| **Статус** | программа в **Draft PR**, runtime Test **не** выкатывался; этот файл обновлён в TASK-25 (PR #28) |
| **Survey** | [MODULAR_REORG_SURVEY.md](MODULAR_REORG_SURVEY.md) |
| **ADR** | [MODULAR_REORG_ADR.md](MODULAR_REORG_ADR.md) |
| **Допуск** | [MODULAR_REORG_ANTARES_WORK_ADMISSION.md](MODULAR_REORG_ANTARES_WORK_ADMISSION.md) |
| **Первый переход Antares** | § «Конечный план первого перехода» ниже; не путать с первым code scope TASK-25 (`/run_wallet` only) |

Merge/deploy **намеренно** не входят в TASK-01…25. Merge в `test_main` может перезапустить Test — см. § Автодеплой.

### Как читать статусы

| Метка | Значение |
|-------|----------|
| **Draft реализовано** | код или принятый docs-контракт в Draft PR; merge **нет** |
| **Sandbox проверено** | именованный прогон на указанном SHA (Cursor и/или GPT). Прогоны **не** складывать в «N уникальных тестов» |
| **Выпущено** | merge в `test_main` / production процесс. Для линейки 01–25: **нет** |

Рабочий production Test по-прежнему **legacy mixed** (`PROJECT_PROFILE` unset). Isolated `python -m apps.antares` в prod **не** запущен.

### Состояние программы (2026-09-21)

| Task | Draft | Sandbox (свои SHA, не сумма) | Выпущено |
|------|-------|------------------------------|----------|
| TASK-02 парсер | код, review, PR #5 | Cursor **41 passed** TASK-02/03 вместе, Python **3.13.14**; GPT **18** тестов на `acfb9958…` (изолированная директория, полный набор не гонял); GPT diff PR #6, набор 41 **не** перезапускал | **нет** |
| TASK-03 early gate | код, review, PR #6, HEAD `48a2a82…` | тот же прогон 41, что строка TASK-02 | **нет** |
| TASK-04 эталон | docs + golden/inventory/harness, review, PR #7, HEAD GPT `443ba70e…` | Cursor **94 passed** на `493c776e…` (inventory/registration/goldens/parser+gate; **не** повтор на `443ba70e…`); отдельно Platform compare **1 passed** на `443ba70e…`; отдельно payin golden **1 passed** на `443ba70e…`. GPT код/diff `443ba70e…`, наборы 94 / 1 / 1 **не** запускал | **нет** |
| TASK-05…11 handlers/jobs | код в PR #8–#14, review | отдельные task-прогоны на своих SHA (не суммировать с 41 / 79 / 15) | **нет** |
| TASK-12/13 ingest | план + код, PR #15/#16 | код ingest GPT `4a1e7796…`; закрытие `5f131ce…` | **нет** |
| TASK-14/15/16 сборка | план + script bind + `assemble_antares`, PR #17–#19 | сборка **не** сервис | **нет** |
| TASK-17 entryplan | docs, PR #20 | pytest не требовался | **нет** |
| TASK-18 boot | код, PR #21, review `2c6eeac…`, close `d1d11e3…` | Cursor **79 passed** на `0603eb9…`, 3.12.10; GPT код/diff, набор не запускал; исторический **85 passed** с fake-append — **другой** SHA/ограничение | **нет** |
| TASK-19 lifecycle plan | docs, PR #22, close `8d80647…` | pytest не запускался | **нет** |
| TASK-20 local rules | код, PR #23, review `8b42e4d…` | Cursor **78 passed** `cd23a7a…`, **31 passed** `8b42e4d…`; GPT набор не запускал | **нет** |
| TASK-21 Application plan | docs, PR #24 | pytest не запускался | **нет** |
| TASK-22 build-only | код, PR #25, review `d9592ff…` | Cursor **81 passed** / **33 boot**, 3.12.10, PTB 22.8, httpx 0.28.1; GPT набор не запускал | **нет** |
| TASK-23 start/stop plan | docs, PR #26, close `94951c6…` | pytest не запускался | **нет** |
| TASK-24 PTB helper | код, PR #27, review `34ef7af…`, close `3649764…` | Cursor **15 / 96 passed** на `34ef7af…`; historical **91/10** `e737281`/`e039e25`, **94/13** `d842912` — границы harness, не «ещё +N тестов к 15»; GPT код/diff, наборы не запускал | **нет** |
| TASK-25 допуск | docs, review пройден, PR #28, HEAD `eda7144…`; 1д=8ч; mixed-stop проверка 2–4д отдельно; cutover **не** к исполнению | pytest не требовался | **нет** |
| Следующая | TASK-26: WorkAdmission + isolated `/run_wallet` | — | — |
| Модули | 17 CommandHandler + Document.ALL в `modules.antares`; ingest owner `document_ingest` | — | **нет** (не в prod) |
| Mixed gate | явный `antares`/`raccoon`/`wr` на `scheduler.py` — отказ **после выката** TASK-03 | — | **нет** |
| Serve / polling isolated | **нет** | sandbox TASK-24 без live getUpdates | **нет** |
| Stop бизнес-потоков | **нет** (worker/sender/executor) | — | **нет** |

Прогоны выше — **разные наборы и SHA**. 41 ≠ 79 ≠ 81 ≠ 15; не складывать.

Draft PR #4…#28 **пока не сливать**.

Перед любым выпуском в `test_main`: окно restart Test; **нет непустого `PROJECT_PROFILE`** у текущего mixed-сервиса (иначе после TASK-03 процесс не стартует).

После согласования выпуска: слить **#4** → retarget **#5** и проверить diff → … по цепочке. Review после retarget **не** автоматический.

`JOB_ACCEPT` и `EXTERNAL_SIDE_EFFECTS` в коде **отсутствуют**. Isolated допуск (TASK-25) — in-process seal, не замена cutover-флага двух процессов.

**Следующий code:** TASK-26 — примитив допуска + `/run_wallet`. Не serve, не полный переход, не mixed-stop.

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

Harness / тесты (не суммировать в одно число):

| Прогон | SHA | Что |
|--------|-----|-----|
| Cursor **94 passed** | `493c776e…` | связанный набор Test: inventory AST, registration subprocess, goldens, parser+gate. **Не** повторялся на `443ba70e…` |
| Cursor **1 passed** | `443ba70e…` | Platform compare: `tests/compare_platform_raccoon_payin.py --platform-checkout` |
| Cursor **1 passed** | `443ba70e…` | Test payin golden (`test_raccoon_payin_format_golden.py`) |
| GPT | `443ba70e…` | код/diff; наборы 94 / Platform 1 / payin 1 **не** запускал |

94, два раза по 1 и прогоны других TASK — **разные** наборы/SHA.

| | |
|--|--|
| Файлы | `ops/MODULAR_REORG_BEHAVIOR_BASELINE.md`; golden; `tests/test_behavior_baseline_inventory.py`; `tests/test_behavior_baseline_registration.py`; Platform compare |
| Сравнение версий | сохранённые входы; Platform — stubs sender/rules, блок сети/потоков |
| `EXTERNAL_SIDE_EFFECTS` | **предложение, кода нет.** Dry-run **не** доказывает отсутствие побочных эффектов |

---

## Этап 3 — выделить Antares, совместимые entry

Регистрация jobs через модуль; mixed legacy остаётся default, пока enforce не включён.  
`JOB_ACCEPT` / drain — **предложение, кода нет**; реализовать **до** cutover, не в TASK-02.

**Сейчас (2026-09-21, Draft, не выпущено):** selective script bind (TASK-15) и `assemble_antares` (TASK-16) есть в Draft. Isolated `boot`/`run` (TASK-18/20/22) собирают Application без polling. Helper `run_ptb_lifecycle` (TASK-24) в sandbox без live Telegram. Допуск работы — **только контракт** TASK-25. Serve/polling, worker/sender/executor stop, isolated schedules **нет**. `dropbox_watcher` по-прежнему грузится при импорте `JOB_REGISTRY`; разделение `job_runner` автоматически не входит.

Совместимость: enforce off = **смешанный** процесс, не «уже antares-only». Isolated entry **не** ослабляет mixed gate.

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
| Isolated entry per profile | Draft: `apps.antares` boot/run + helper; **serve нет**; **не выпущено** | `raccoon`/`wr` не на mixed scheduler | до первого перехода Antares (serve) |
| Isolated work admission | docs PR #28; **кода нет** | seal новой работы | до serve; первый code = `/run_wallet` only |

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

Пока рычага прекращения приёма нет, **idle + stop не штатный путь**: idle при открытых входах подвержен гонке (новый `submit`/`put` после «уже idle»). Isolated `seal` этого не закрывает. Не считать cutover-план готовым к исполнению.

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

## Конечный план первого перехода Antares

Это переход **mixed Test → isolated Antares** на токене/кабинетах Antares. Не порт Raccoon, не WR, не «урезанный бот».

Первый **code** TASK-25 (`WorkAdmission` + `/run_wallet`) — **не** этот переход. Переход требует допуска **всех включённых путей**, иначе обходы (ingest, Auto-Enable, прочие `/run_*`) принимают работу после seal.

### Состав первого выпуска (не сокращать)

Взято из сборки TASK-16 и mixed Antares-функций. Не выкидывать ingest/Auto-Enable/registry «чтобы быстрее».

| Область | Входит в первый выпуск | Источник |
|---------|------------------------|----------|
| Команды | 17: `start`, `help`, `status`, `whoami`, `reload_rules`, `run_wallet`, `run_hourly`, `run_download`, `run_rate`, `operator_wallets_ready`, `rules_validate`, `auto_enable_plan`, `auto_enable_run`, `wallet_editor_refresh`, `registry_health`, `registry_replay`, `registry_export` | `get_antares_handlers()` |
| Ingest | `Document.ALL` → WE worker queues | `document_ingest` |
| Jobs | семь keys: `download`, `hourly`, `rate`, `wallet`, `wallet_editor_registry_refresh`, `wallet_editor_registry_replay`, `script_job:operator_wallets_ready` | `assemble_antares` |
| Расписания | isolated loop **только** этих семи keys; skip unknown / non-Antares rows | LIFECYCLE.md |
| Прямые ops | registry replay/export, Auto-Enable plan/run, reload_rules | handlers без `request_job` |
| Sender | исходящие отчёты/ошибки jobs | `telegram_bot` (stop API ещё нет — блокер этапа B) |
| Read-only | whoami/help/start/status/registry_health/rules_validate | не постановка |

**Не входят** в первый выпуск Antares: Raccoon commands/jobs, `hello_world`, conversion bridge как mixed/Raccoon путь, WR, dual polling.

### Обязательные блокеры vs можно отложить

**Блокеры перехода** (без них mixed остаётся единственным исполнителем):

1. Допуск на **всех** путях состава выше, включая ingest `put_nowait`, прямые ops, schedules `submit`, внутренний `enqueue_auto_enable_batch` (или эквивалент isolated).
2. Завершение **принятой** работы и остановка ресурсов: worker join/sentinel, production `ThreadPoolExecutor.shutdown`, sender stop, PTB stop/shutdown (helper TASK-24 есть в Draft, не выпущен).
3. Публичный `serve`: initialize → start → `open()` → polling → `request_antares_stop` (seal затем `stop.set()` / `call_soon_threadsafe`).
4. Проверка конфигурации и состояния: token только у одного процесса; `PROJECT_PROFILE`/startCommand isolated; rules snapshot политика; `STATE_DIR`/locks; WE allowlist; PG/Dropbox; anti-restart старого mixed.
5. Интеграционная проверка на не-prod токене или объявленном окне (команды, ingest, один schedule tick, stop/drain).
6. Переключение и откат: stop mixed после idle, единственный isolated ACTIVE, runbook отката SHA.

**Можно отложить после первого выпуска** (не выкидывая функции выпуска):

- Durable Telegram inbox / schedule cursor — только если окно явно объявляет потерю *ещё не принятых* updates и пропуск слотов (§ Cutover 4–5). Принятая работа всё равно должна дожиматься.
- `JOB_ACCEPT` **env** двух процессов — isolated in-process seal закрывает новый приём; env остаётся рычагом cutover, если старый mixed ещё жив отдельно.
- `EXTERNAL_SIDE_EFFECTS`, split `job_runner`, отдельный prod `STATE_DIR` если текущий каталог согласован.
- Raccoon/WR isolated, soak Platform.
- Reservation-токен ingest (admit до `get_file`).

Откладывать **сам ingest / Auto-Enable / registry / schedules семи keys** нельзя: это действующие Antares-функции mixed.

### Последовательность этапов

#### A. Допуск всех включённых путей

| | |
|--|--|
| Результат | `seal()` ⇒ нет нового `submit`/`put_nowait`/входа в прямую ops на составе выпуска, включая внутренний Auto-Enable enqueue |
| Зависимости | контракт TASK-25; примитив; затем остальные TG jobs, ingest, прямые ops, isolated schedule dispatch |
| Готовность | тесты Event/barrier на **каждом** подключённом submit/put; список обходов пуст **для состава выпуска**; mixed unbound без изменений |
| Не готовность | только `/run_wallet` (это TASK-25 code, не переход) |

#### B. Завершение принятой работы и остановка ресурсов

| | |
|--|--|
| Результат | после seal слой D дожимается или durable-handoff; затем join worker, shutdown executor, stop sender, PTB stop/shutdown; idle: `_RUNNING` пуст, WE queues пусты **или** handoff записан |
| Зависимости | A; production stop API (сейчас нет); helper TASK-24 для PTB |
| Готовность | сценарий: Accepted Future переживает seal; timeout дожима → эскалация, не silent kill Save |
| Не готовность | kill процесса / только `app.stop` |

#### C. Serve и сигналы

| | |
|--|--|
| Результат | `python -m apps.antares serve` (имя argv — code PR): bind до initialize, `open` после start, polling, stop через `request_antares_stop` |
| Зависимости | A (иначе polling кормит обходы); B хотя бы API stop; STARTSTOP.md порядок initialize→start→polling |
| Готовность | SIGINT/loop-thread `set`; другой поток: `seal()` затем `loop.call_soon_threadsafe(stop.set)`; Windows SIGTERM — UNKNOWN, не обещать |
| Не готовность | `run_polling` как isolated; `enable_polling=True` без serve-контракта |

#### D. Проверка конфигурации и состояния

| | |
|--|--|
| Результат | один token; startCommand isolated; нет второго polling; rules/STATE_DIR/WE/PG согласованы; anti-restart mixed проверен **на этих** сервисах |
| Зависимости | Railway connector факты; чеклист Cutover «подтверждение остановки» |
| Готовность | записанные UUID сервисов, SHA, env без случайного `PROJECT_PROFILE` на старом mixed до окна |
| UNKNOWN | pin SHA в Railway; живые prod rows workbook; фактический restart policy |

#### E. Интеграционная проверка

| | |
|--|--|
| Результат | на выделенном токене или объявленном окне: `/whoami`, `/run_wallet`, ingest .xlsx, registry_health, один schedule key, затем seal+drain+stop |
| Зависимости | C+D; stubs или реальный кабинет по решению ops |
| Готовность | нет getUpdates conflict; нет чужих JOB_REGISTRY keys; отчёты уходят; после stop нет новых `job_started` |
| Не готовность | только unit TASK-24 `/whoami` sandbox |

#### F. Переключение и откат

| | |
|--|--|
| Результат | mixed drain (порядок § «Остановка старого mixed») → stop + anti-restart → isolated единственный ACTIVE; откат: сначала stop isolated (A+B isolated), затем старый SHA |
| Зависимости | E; **остановка mixed не следует из isolated `seal`** (ниже); § Cutover (locks не кластерные; dual poll запрещён) |
| Готовность | runbook с deployment id, временем передачи, checklist формата данных для отката |
| Не готовность | два ACTIVE на одном токене; откат при нечитаемом новом формате state без миграции |

Порядок A→B→C→D→E→F **обязателен по смыслу**: serve без A оставляет обходы; switch без B рвёт Save; switch без D/E — ops-лотерея.

### Остановка старого mixed (не isolated seal)

Isolated `WorkAdmission` живёт в процессе `python -m apps.antares`. Mixed `scheduler.py` **не** bind'ит допуск: `seal()` isolated **не** прекращает приём на старом Test.

Безопасная остановка **старого** исполнителя — отдельная цепочка **в том же mixed-процессе**:

1. **Прекращение приёма** — новые TG jobs, ingest `queue.put`, Auto-Enable, `schedule_loop` → `dispatch_job_background`, conversion bridge не ставят работу.
2. **Завершение принятого** — `_RUNNING`, WE queues, executor futures, sender дожимаются или durable-handoff; не freeze очереди.
3. **Stop** — выйти из `run_polling`, остановить schedule thread, join worker, shutdown executor, stop sender. Сейчас у mixed **нет** этих API (`schedule_loop` = `while True`; worker daemon; sender без production stop).
4. **Anti-restart** — replicas=0 / stop **этого** service UUID и проверка, что контейнер не поднимается (Cutover § anti-restart). Isolated seal шага 4 **не** делает.

Пока шага 1 нет, «подождать idle и убить контейнер» рвёт Save — не штатный переход.

**Будущий scope (не TASK-25, не первый `/run_wallet`):** рычаг допуска на **legacy mixed** (`JOB_ACCEPT` или эквивалент в `schedule_loop` / mixed handlers / ingest / Auto-Enable). Правка общего `request_job` без default-unbound заденет isolated и тесты — отдельно проектировать. **Сейчас не реализовывать.**

Нерешённые вопросы остановки mixed:

- Где ставить mixed-gate, чтобы не сломать unbound-тесты и не считать isolated seal достаточным.
- Как наблюдать idle без join API (логи `job_started` — слабый сигнал).
- Доказанный anti-restart на service UUID Test.
- Длина drain при нескольких WE-профилях.
- Нужен ли durable inbox, если окно объявляет потерю только *непринятых* updates.

---

## Оценка оставшейся работы (диапазоны, не дата)

Ёмкость по **категориям**, не календарный дедлайн. **1 инженерный день = 8 человеко-часов.** Строки не складывать в одну цифру «до F». Прежние **15–35 инж.-дней** смешивали категории и **сняты**. Общий cutover-план **не** готов к исполнению.

| Строка | Разработка | Проверка (unit/barrier) | Интеграция | Внешние ожидания | Экспл. окно |
|--------|------------|-------------------------|------------|------------------|--------------|
| TASK-25: примитив + `/run_wallet` | **1–2** дн. | **0.5–1** дн. | 0 | review PR | **0** |
| A: остальные пути состава | **3–6** | **2–4** | 0 | — | 0 |
| B: isolated drain/stop API | **4–8** | **2–4** | 0 | — | 0 |
| Mixed stop (будущий legacy-scope) | **3–6** | **2–4** | 0 | anti-restart Railway | 0 до F |
| C: serve/сигналы | **2–4** | **1–2** sandbox | не-prod token **1–2** | Windows SIGTERM UNKNOWN | не prod |
| D: конфиг/runbook | **1–3** | сверка Railway **1–2** | 0 | pin SHA / restart policy UNKNOWN | в F |
| E: интеграционные сценарии | фиксы **1–3** | — | **2–5** | кабинет/токен ops | staging опц. |
| F: переключение | runbook **0.5–1** | dry-run стопа **0.5–1** | — | anti-restart после **закрытых** входов mixed | **2–6 ч** стены |
| Merge #4…N + retarget | 0 feature | сверка diff **1–3** на каждый retarget | — | очередь review + **restart Test** на каждый merge в `test_main` | отдельные окна, не F |

Суммы **только разработки** (TASK-25+A+B+C+D+E-фиксы+F, **без** mixed-legacy): **12.5–27** инж.-дней (100–216 ч).  
С mixed-legacy stop: **15.5–33** разработки (124–264 ч).  
Проверка barrier/unit isolated: **7–14** (56–112 ч).  
Проверка mixed-stop: **2–4** инж.-дня (**16–32 ч**) — **отдельная** строка, не внутри isolated `/run_wallet`.  
Интеграция E: **2–5** (+ не-prod C **1–2**).  
Внешнее: review, Railway anti-restart, retarget, UNKNOWN — **не** входят в суммы разработки.  
Окно F: **2–6 часов**, не дни разработки.

Критический путь перехода: **прекращение приёма mixed** → drain → stop → anti-restart **параллельно** isolated **A → B → C → E → F**. Isolated `/run_wallet` — только первый камень A. Isolated `seal` **не** заменяет mixed-stop. **Idle без закрытых входов не замена** шагу прекращения приёма: новые постановки гоняются с «уже idle». Cutover-план **не** к исполнению.

### Допущения

- Один isolated процесс Antares **в репозитории Test** (Draft PR/worktree); отдельный репозиторий не создаём; mixed gate не ослабляют.
- Состав выпуска = таблица выше, без вырезания WE.
- PTB 22.8, SimpleUpdateProcessor, без JobQueue extra, пока extra не войдёт отдельным решением.
- Прогоны остаются привязаны к SHA; новый code не «наследует» 15/96 как покрытие допуска.
- Окно F одно; dual poll запрещён.

### UNKNOWN (двигают верх диапазона, не дату)

- Проверенный anti-restart Railway на **этом** service UUID.
- Pin SHA / restart policy.
- Prod workbook schedule rows (какие из семи keys реально enabled).
- SIGTERM на Windows serve.
- Нужен ли отдельный `STATE_DIR` до F.
- Совместимость формата outbox/registry при откате.
- Сколько живых WE очередей/профилей в момент окна (длина drain).

### Нерешённые блокеры перехода (сейчас)

- Нет кода допуска (даже `/run_wallet`).
- Нет допуска на ingest, прямых ops, schedules, internal enqueue (isolated A).
- Isolated `seal` **не** останавливает mixed.
- Нет mixed-рычага «прекратить приём → дожать → stop» (будущий legacy-scope, не TASK-25).
- Нет production stop worker/sender/executor (isolated и mixed).
- Нет isolated serve/polling.
- Anti-restart mixed **не доказан**.
- Durable inbox нет (переход без него только с объявленной потерей непринятых updates).
- Ни один Draft PR линейки не слит. Реорганизация **остаётся в репозитории Test** (Draft PR/worktree); отдельный репозиторий Antares **не** создаём.

---

## Следующие задачи (актуально)

Не merge #4–#28 в этом PR. Не начинать code, пока TASK-25 на review.

1. После review TASK-25: code примитива + `/run_wallet` (узкий scope). Остальные пути — обходы до этапа A.
2. Затем code допуска остальных путей состава выпуска (A), не «заодно» serve.
3. Stop API isolated (B) и serve (C) — отдельные PR.
4. Mixed stop (прекращение приёма → drain → stop → anti-restart) — **отдельный будущий scope**, не первый code TASK-25.
5. Raccoon/WR isolated — после первого перехода Antares, не вместо него.

Устарело как «следующий code»: split `script_jobs` (сделан в TASK-15 Draft) и «кода сборки нет» (TASK-16 Draft).
