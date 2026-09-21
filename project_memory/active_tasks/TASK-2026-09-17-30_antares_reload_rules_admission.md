# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-30 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-29 (PR #32, review `71fce3b9eb4f621164799dd8ed379140fd3ebf5a`, закрытие `d3eecc0bb9dd5d069d1a4f1b169026303fdff393`) |
| **PR** | Draft [#33](https://github.com/deniskotdavydov1991-wq/Test/pull/33) `feat/task-2026-09-17-30-antares-reload-rules-admission`, base `feat/task-2026-09-17-29-antares-registry-replay-admission` |
| **Риск** | medium: isolated `/reload_rules` уходит в общий job executor; `AccessRules` / `rules_provider` без lock — прежняя гонка чтения/записи snapshot |

Isolated `/reload_rules`: bind → ACL → `submit_if_open(get_job_executor(), _reload_bound_rules, bound AccessRules)` → наблюдение Future до первого await → прежний ответ. Стартового «Запускаю» нет. Mixed/unbound — прежний sync в callback. `request_job` / новый `job_type` не используются. Provider и mixed **не** переписывались. Перенос **не** выпущен.

---

## Goal

Допустить внешний Telegram-вход `/reload_rules` тем же generic admission, что TASK-28/29.

---

## Обследование конкурентного доступа

| Объект | Блокировки | Поведение |
|--------|------------|-----------|
| `AccessRules.invalidate` / `get_snapshot` | нет | `_snap = None` / присваивание **одной** ссылки на готовый `Snapshot` |
| `get_snapshot_v2` | нет | три раздельных записи: `_last_v2_stat`, `_last_v2_snapshot`, `_last_v2_decision` |
| `get_indexes_v2` | нет | после `get_snapshot_v2` пишет `_last_indexes*` отдельно; `_last_indexes_stat` берёт **текущий** `_last_v2_stat` |
| `invalidate` AccessRules | — | **не** вызывает `invalidate_rules_v2_cache` |
| ACL `check_access` | — | `get_snapshot()` без `force_sync`; это **не** reload |
| `request_scheduler_clocks_reset` | Event + lock на reason | не защищает snapshot |

Атомарное присваивание одной ссылки (`_snap = snap` или `_last_v2_snapshot = …`) **не** согласует набор provider globals. Воспроизведено:

1. Старый `AccessRules.get_snapshot()` заканчивает публикацию `_snap` **после** принятого reload `force_sync=True`: ответ Telegram может быть v2, а bound instance уже снова v1.
2. Старый `get_indexes_v2` строит indexes по v1, затем пишет `_last_indexes_stat = _last_v2_stat` уже от v2.

Очередь **только** reload и lock **только** вокруг callable reload **не** закрывают ACL/`get_snapshot`/`get_indexes_v2`/`request_job`, которые очередь обходят. Наличие той же гонки в mixed **не** делает её допустимой.

Откат `invalidate` при ошибке snapshot **не** обещается.

### Ограниченный вариант (не внедряется в TASK-30)

Участники одной синхронизации: writers `get_snapshot_v2` (publish трёх `_last_v2_*` одним объектом/критической секцией), `get_indexes_v2`, `invalidate_rules_v2_cache`, `AccessRules.invalidate`/`get_snapshot`; readers — те же плюс `get_rules_snapshot` (читает `_last_v2_*` для version).

Deadlock: `get_indexes_v2` вызывает `get_snapshot_v2`. Нужен один `RLock` на publish-кэш **или** вложенный вход без второго lock. Download / parse / audit / identity save — **вне** lock (compute, затем короткая публикация). Не держать lock на I/O.

Lock только в `/reload_rules` недостаточен. Очередь только reload недостаточна.

Mixed: тот же lock в `rules_provider` сериализует и loop-reload, и job-readers — это изменение timing для всех профилей, не только isolated Antares.

**Блокер TASK-30:** правка кэша — cross-cutting `rules_provider` + `AccessRules` (Antares/Raccoon/WR), с I/O вне секции и регрессией mixed. Отдельный scope: атомарный publish-struct (stat+snapshot+decision+indexes) под одним RLock; callers не меняют API `force_sync`. Широкая переработка в этом PR не делается.

---

## Success Criteria

- [x] Isolated: ACL → submit того же bound `AccessRules` → invalidate → `get_snapshot(force_sync=True)` → reset только после успеха
- [x] Closed/sealed/ACL deny/ошибка submit: нет invalidate / force reload / reset
- [x] Mixed/unbound: прежний sync порядок и ответы, без executor
- [x] Provider не переписан; гонки воспроизведены (`test_repro_*`), не выданы за safety
- [ ] GPT review; merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/unit/test_tg_registry_export.py` **143 passed**, exit 0; отдельно `tests/test_scheduler_clocks_reset.py` **7 passed**, exit 0; отдельно `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**, exit 0; Python 3.12.10 |
| GPT | ещё не ревьюил |

---

## Out Of Scope

ingest, schedules, самостоятельные новые постановки, drain, serve, mixed-stop, merge, retarget, deploy, исходное Test, Raccoon/Windrose, перенос `docs/modular-reorg-project-contract`, lock/rewrite `rules_provider`.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | isolated `/reload_rules` admission; Draft PR #33; статус **review** |
| 2026-09-21 | уточнение конкурентного контракта: ACL/force_sync, repro stale `_snap` и torn indexes; provider не чинится |
