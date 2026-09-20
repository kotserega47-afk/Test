# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-17 |
| **Статус** | review (ожидает GPT; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-16 (PR #19, review `f29cc8918eef022e0a4b88b1ac7f59a67212798f`, закрытие `a05ee7f7d06d3dd801b58181de963d4832d9bfd9`, pin `950a66a745bbac64cbf2281951a5ed28e01474cf`), `ops/MODULAR_REORG_ANTARES_ENTRYPOINT.md` |
| **PR** | Draft [#20](https://github.com/deniskotdavydov1991-wq/Test/pull/20) `feat/task-2026-09-17-17-antares-entrypoint`, base `feat/task-2026-09-17-16-antares-assembly` |
| **HEAD** | `abb8a284feaa94d22d4137e6544776d9c88e4225` |
| **Риск** | low: только документы |

План отдельного Antares process entry **подготовлен к review**. Runtime, mixed gate, Railway и профили **не** менялись. Isolated entrypoint, polling, worker, schedules, `JOB_ACCEPT` и cutover **не** реализованы. TASK-18 **не** начат.

На текущем коде `assemble_antares` → `antares_job_executors` **уже** импортирует `telegram_bot` (wallet via transport; rate; registry replay/refresh). Boot без sender возможен только с lazy-import на этих границах. Stub TASK-16 не доказательство. Успех boot = диагностика + exit 0, без idle.

---

## Goal

Зафиксировать выполнимый контракт isolated `-m apps.antares`: профиль из окружения до dotenv, размыкание sender, запрет импорта в harness, единственное завершение процесса после сборки, минимальный TASK-18 scope.

---

## Current Behavior (исходники на pin TASK-16)

- Единственный process entry: `python scheduler.py`; prod `tini` + тот же файл.
- Mixed gate до Telegram/jobs: явный `antares`/`raccoon`/`wr` → exit 2; isolated этот gate **не** ослабляет.
- `telegram_bot`: `load_dotenv()` и daemon sender loop на **import**.
- `assemble_antares` / `antares_job_executors()` тянут sender: `downloader_wallets` → `telegram_transport`; плюс прямые import в bakai и registry/refresh.
- `hourly` / `download` обёртки в `jobs.py` откладывают import до запуска job; `downloader.py` при запуске download всё ещё module-level `telegram_bot`.
- `load_schedules` без фильтра job_type; locks в `STATE_DIR/locks`.

---

## Desired Behavior

Документ — контракт. Код в TASK-17 **не** пишется. TASK-18: boot + lazy sender § 5.3 плана, затем exit 0.

---

## Success Criteria

- [x] Команда `py -3.12 -m apps.antares`; пакет `apps`; корень в `sys.path` без пользовательского PYTHONPATH
- [x] Профиль из `os.environ` до dotenv; parser as-is; `.env` только `{repo_root}/.env`, `override=False`, без обхода родителей
- [x] Token после dotenv; whitespace = пусто
- [x] Цепочки sender на шести jobs; необходимое lazy-import; без обещания boot без sender на текущем коде
- [x] Успех сборки → диагностика → exit 0; нет idle/polling
- [x] Harness: запрет загрузки `telegram_bot`/mixed/raccoon, не заранее stub в `sys.modules`; проверка после успеха и при ошибках
- [x] Mixed gate без изменений; ослабление для isolated entry **не** требуется
- [ ] GPT review: блокирующих нет
- [ ] merge/deploy (намеренно открыто)
- [ ] TASK-18 boot code (отдельное задание, не начато)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы; исходники pin `950a66a…` / уточнение после `40d84b2…`; pytest **не** требовался |
| GPT | ещё не проверял этот уточнённый diff |

---

## Out Of Scope

runtime/тесты TASK-17; реализация `apps/` в этом PR; polling; worker; schedules; ослабление mixed gate; Railway; `JOB_ACCEPT`; cutover; merge/retarget/deploy; исходное дерево Test / ветка Wallet Editor; старт TASK-18.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | план isolated Antares entrypoint подготовлен к review; Draft PR #20 |
| 2026-09-20 | уточнён выполнимый boot: цепочки sender, harness-запрет, exit 0, dotenv/профиль |
