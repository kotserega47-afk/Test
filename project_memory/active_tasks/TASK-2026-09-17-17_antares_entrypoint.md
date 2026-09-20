# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-17 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-16 (PR #19, review `f29cc8918eef022e0a4b88b1ac7f59a67212798f`, закрытие `a05ee7f7d06d3dd801b58181de963d4832d9bfd9`, pin `950a66a745bbac64cbf2281951a5ed28e01474cf`), `ops/MODULAR_REORG_ANTARES_ENTRYPOINT.md` |
| **PR** | Draft [#20](https://github.com/deniskotdavydov1991-wq/Test/pull/20) `feat/task-2026-09-17-17-antares-entrypoint`, base `feat/task-2026-09-17-16-antares-assembly` |
| **HEAD (проверен GPT)** | `ed3cbaf2b240786c7985108dac9a7cd775f2b871` |
| **Закрытие docs** | (этот коммит на PR #20) |
| **Риск** | low: только документы |

План isolated Antares entry **принят**. Runtime, mixed gate, Railway и профили **не** менялись. Isolated entrypoint, polling, worker, schedules, `JOB_ACCEPT` и cutover **не** реализованы. Review **пройден** на HEAD `ed3cbaf…`. PR #20 остаётся Draft. Реализация boot — TASK-18.

Команда: `python -m apps.antares` из корня (cwd уже на `sys.path`; ручная правка `sys.path` не нужна). Профиль только в `.env` при отсутствии `PROJECT_PROFILE` в окружении процесса → exit 2.

---

## Goal

Зафиксировать выполнимый контракт isolated `-m apps.antares`: профиль из окружения до dotenv, размыкание sender, запрет импорта в harness, единственное завершение процесса после сборки, минимальный TASK-18 scope.

---

## Success Criteria

- [x] Команда `python -m apps.antares` из корня; пакет `apps`; без правки `sys.path`
- [x] Профиль из `os.environ` до dotenv; отсутствие ключа в процессе → exit 2 даже если `.env` содержит `antares`
- [x] Token после dotenv; whitespace = пусто
- [x] Цепочки sender; необходимое lazy-import в TASK-18
- [x] Успех сборки → диагностика → exit 0; нет idle/polling
- [x] Harness: запрет загрузки `telegram_bot`/mixed/raccoon
- [x] Mixed gate без изменений
- [x] GPT review HEAD `ed3cbaf…`: блокирующих нет
- [ ] merge/deploy PR #20 (намеренно открыто)
- [ ] TASK-18 boot code (отдельное задание)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | Документы; pytest **не** требовался |
| GPT | Проверил план HEAD `ed3cbaf…`. Тесты **не** запускал. |

---

## Out Of Scope

runtime/тесты TASK-17; реализация `apps/` в этом PR; polling; worker; schedules; ослабление mixed gate; Railway; `JOB_ACCEPT`; cutover; merge/retarget/deploy; исходное дерево Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-20 | план isolated Antares entrypoint подготовлен к review; Draft PR #20 |
| 2026-09-20 | уточнён выполнимый boot: цепочки sender, harness-запрет, exit 0, dotenv/профиль |
| 2026-09-20 | GPT review HEAD `ed3cbaf…`: план принят; merge/deploy нет |
| 2026-09-20 | документационное закрытие на ветке PR #20 |
