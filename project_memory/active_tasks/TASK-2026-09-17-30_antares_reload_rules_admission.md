# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-30 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-29 (PR #32, review `71fce3b9eb4f621164799dd8ed379140fd3ebf5a`, закрытие `d3eecc0bb9dd5d069d1a4f1b169026303fdff393`) |
| **PR** | Draft (этот PR) `feat/task-2026-09-17-30-antares-reload-rules-admission`, base `feat/task-2026-09-17-29-antares-registry-replay-admission` |
| **Риск** | medium: isolated `/reload_rules` уходит в общий job executor; `AccessRules` / `rules_provider` без lock — прежняя гонка чтения/записи snapshot |

Isolated `/reload_rules`: bind → ACL → `submit_if_open(get_job_executor(), _reload_bound_rules, bound AccessRules)` → наблюдение Future до первого await → прежний ответ. Стартового «Запускаю» нет. Mixed/unbound — прежний sync в callback. `request_job` / новый `job_type` не используются. Provider и mixed **не** переписывались. Перенос **не** выпущен.

---

## Goal

Допустить внешний Telegram-вход `/reload_rules` тем же generic admission, что TASK-28/29.

---

## Обследование конкурентного доступа

| Объект | Блокировки | Поведение |
|--------|------------|-----------|
| `AccessRules.invalidate` / `get_snapshot` | нет | `_snap = None` / присваивание готового `Snapshot`; `invalidate` **не** вызывает `invalidate_rules_v2_cache` |
| `rules_provider.get_snapshot_v2` / `get_rules_snapshot` | нет | модульные `_last_v2_*` / `_last_rules_wb` без `Lock` |
| `request_scheduler_clocks_reset` | `threading.Event` + lock на reason | безопасен, если `schedule_loop` не крутится |
| ACL `check_access` | — | `get_snapshot()` без `force_sync`; это **не** reload |
| Mixed сегодня | — | reload на потоке loop, job readers на `get_job_executor()` — та же гонка уже есть |
| Isolated TASK-30 | — | reload на том же executor, что `request_job` (`max_workers>1` → overlap с другими jobs) |

Конкретная гонка (не чинится этим PR): поток A `invalidate` + `get_snapshot(force_sync=True)` пишет `_snap` и provider globals; поток B `get_snapshot()` в середине может last-write-win чужим полным snapshot. Один успешный тест overlap **не** доказывает thread safety.

Ограниченный вариант позже (не здесь): lock вокруг cache `get_snapshot_v2` **или** очередь reload на одном worker. Широкая переработка provider в TASK-30 не делается.

Откат `invalidate` при ошибке snapshot **не** обещается: прежняя семантика восстановления правил не меняется.

---

## Success Criteria

- [x] Isolated: ACL → submit того же bound `AccessRules` → invalidate → `get_snapshot(force_sync=True)` → reset только после успеха
- [x] Closed/sealed/ACL deny/ошибка submit: нет invalidate / force reload / reset
- [x] Mixed/unbound: прежний sync порядок и ответы, без executor
- [x] Provider не переписан; гонка задокументирована
- [ ] GPT review; merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/unit/test_tg_registry_export.py` **141 passed**, exit 0; отдельно `tests/test_scheduler_clocks_reset.py` **7 passed**, exit 0; отдельно `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**, exit 0; Python 3.12.10 |
| GPT | ещё не ревьюил |

---

## Out Of Scope

ingest, schedules, самостоятельные новые постановки, drain, serve, mixed-stop, merge, retarget, deploy, исходное Test, Raccoon/Windrose, перенос `docs/modular-reorg-project-contract`, lock/rewrite `rules_provider`.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | isolated `/reload_rules` admission; статус **review** |
