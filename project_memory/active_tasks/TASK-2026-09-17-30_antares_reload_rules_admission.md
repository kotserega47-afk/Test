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

**Блокер TASK-30:** согласованная публикация — контракт TASK-31 принят (`8ef2838…`); code TASK-32 review GPT на `bb25f73433297a0179a3606f640236f7b3038463`. TASK-30 **не** закрыт. Проверка зависимости ниже относится к **объединённому HEAD ветки TASK-32** (PR #35), не к старому HEAD PR #33. SHA `1eefc54720ccd036451f7c3c0e7dadaedf6efb98` **сам по себе исправленного provider не содержит**. Зелёные `test_repro_*` на `1eefc54` — история дефекта.

---

## Success Criteria

- [x] Isolated: ACL → submit того же bound `AccessRules` → invalidate → `publish_with_outcome(force_sync=True)` → `get_snapshot(False)` → reset только после успеха
- [x] Closed/sealed/ACL deny/ошибка submit: нет invalidate / force reload / reset
- [x] Mixed/unbound: прежний sync порядок и ответы, без executor
- [x] Повторная проверка reload на объединённом HEAD TASK-32 (PR #35); PR #33 `1eefc54` не содержит этого provider; 130 на `bb25f734…`, 161 на этом коммите
- [ ] GPT review отчёта зависимости; merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | `py -3.12 -m pytest tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_job_dispatch.py tests/test_behavior_baseline_inventory.py tests/test_behavior_baseline_registration.py tests/unit/test_tg_registry_export.py` **143 passed**, exit 0; отдельно `tests/test_scheduler_clocks_reset.py` **7 passed**, exit 0; отдельно `tests/unit/test_antares_lifecycle.py tests/unit/test_antares_boot.py` **52 passed**, exit 0; Python 3.12.10 |
| Cursor | зависимость reload на объединённом HEAD TASK-32 `bb25f73433297a0179a3606f640236f7b3038463`: `tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_access_guard_commands_map.py tests/test_scheduler_clocks_reset.py` **130 passed**, exit 0, Python 3.12.10. Старый HEAD PR #33 не содержит этого provider. |
| Cursor | объединённый reload-набор на PR #35 (этот коммит, поверх `7d3a463…`): `tests/rules_v2/test_gen_rules_publish.py tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_access_guard_commands_map.py tests/test_scheduler_clocks_reset.py` **161 passed**, exit 0, Python 3.12.10. Runtime не менялся. Исторические **130** на `bb25f734…` сохранены, наборы не суммировать. |
| GPT | ещё не ревьюил этот отчёт зависимости |

---

## Проверка зависимости на объединённом HEAD TASK-32

Проверка относится к объединённому HEAD ветки TASK-32 / PR #35. Старый HEAD PR #33 `1eefc54720ccd036451f7c3c0e7dadaedf6efb98` **сам по себе исправленного provider не содержит**. TASK-30 **остаётся открытым** до отдельного review этого отчёта. TASK-32 закрыт `7d3a4636d333b68a4106a5fbd4974590debf3cd7` — не переоткрыт.

Исторический прогон на `bb25f73433297a0179a3606f640236f7b3038463` (без gen-набора): **130 passed**, exit 0.

Команда (Python 3.12.10), этот коммит (runtime TASK-32 + тесты reload):

```
py -3.12 -m pytest tests/rules_v2/test_gen_rules_publish.py tests/unit/test_antares_work_admission.py tests/test_antares_handlers.py tests/test_access_guard_commands_map.py tests/test_scheduler_clocks_reset.py -q --tb=short
```

Результат: **161 passed**, exit 0.

| Утверждение | Точный тест | Реальные функции / stubs |
|-------------|-------------|--------------------------|
| closed / sealed / ACL deny без force reload и reset | `test_isolated_reload_closed_sealed_no_mutate`, `test_isolated_reload_acl_deny_snapshot_is_not_reload`, `test_isolated_reload_reject_does_not_force_sync_access_rules` (closed), `test_cmd_reload_rules_guard_denies_no_reset`, `test_gen_g9_closed_and_acl_deny_real_handler` | fake AccessRules; G9 stub `publish_with_outcome` boom; mixed — patch `guard_or_deny` / `RULES.invalidate` |
| `fresh_commit` даёт reset | `test_isolated_reload_open_order_identity_no_start`, `test_isolated_reload_real_access_rules_force_sync_publishes` | stub `publish_with_outcome` → `fresh_commit`; stub `get_published_state` + `publish_with_outcome`. Mixed: `test_unbound_reload_stays_on_loop_without_executor`, `test_cmd_reload_rules_success_calls_reset` патчат `get_snapshot` / `RULES`, не outcome |
| допустимый `existing` даёт reset | `test_review_isolated_existing_resets_clocks` | реальный provider + sandbox; реальный `_reload_bound_rules`; stub только clocks; `before_commit_section` держит попытку |
| `stale_reuse` без reset | `test_gen_g5_g16_isolated_reject_and_stale` | реальный `_reload_bound_rules`; `evaluate_snapshot_publish` заменён контролируемым build-fail → `IsolatedReloadNotApplied`; monkeypatch `request_scheduler_clocks_reset` |
| точный `ContractPublishRejected` без reset | `test_gen_g5_g16_isolated_reject_and_stale` | реальный `_reload_bound_rules`; `evaluate_snapshot_publish` заменён контролируемым `ContractPublishRejected`; monkeypatch clocks. Mixed: `test_cmd_reload_rules_failure_does_not_call_reset` (`RULES.get_snapshot` → reject). `test_isolated_reload_snapshot_failure_skips_reset` — `RuntimeError` из stub `publish_with_outcome`, не этот тип |
| `conflict_exhausted` без reset | `test_isolated_reload_conflict_exhausted_skips_reset` | реальный provider, sandbox, `_reload_bound_rules(AccessRules())`; `before_commit_section` → `invalidate_rules_v2_cache` перед каждым commit; цепочка provider → helper; `publish_with_outcome` **не** подменён исключением; monkeypatch clocks. `test_gen_g23_conflict_exhausted` — тот же hook, но только `get_published_state`, без helper/reset |
| старый reader не откатывает published | `test_repro_stale_access_rules_publish_after_reload`, `test_repro_provider_indexes_stat_tears_from_stale_publisher`, `test_gen_g1_g12_old_reader_does_not_store_over_new` | stub `get_published_state` в admission repro; G1 — реальный provider |
| mixed reload сохраняет принятую семантику | `test_unbound_reload_stays_on_loop_without_executor` (`invalidate` → `get_snapshot(True)` → reset, без executor); `test_reload_replaces_snapshot_without_rebind` | stub AccessRules / `get_published_state`; mixed clocks — patch `RULES` |

`tests/test_access_guard_commands_map.py` в наборе прошёл; отдельных сценариев reload/reset там нет.

---

## Out Of Scope

ingest, schedules, самостоятельные новые постановки, drain, serve, mixed-stop, merge, retarget, deploy, исходное Test, Raccoon/Windrose, перенос `docs/modular-reorg-project-contract`, **code** `rules_provider` (контракт — TASK-31).

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-21 | isolated `/reload_rules` admission; Draft PR #33; статус **review** |
| 2026-09-21 | уточнение конкурентного контракта: ACL/force_sync, repro stale `_snap` и torn indexes; provider не чинится |
| 2026-09-21 | зависимость от TASK-31 (публикация поколений); TASK-30 не закрыт |
| 2026-09-22 | контракт TASK-31 принят (`8ef2838…`); TASK-30 всё ещё blocked до review code TASK-32 и повторной проверки reload |
| 2026-09-22 | TASK-32 GPT review на `bb25f734…`; повторная проверка reload на объединённом HEAD PR #35 (**130 passed**); TASK-30 не закрыт; PR #33 `1eefc54` без исправленного provider |
| 2026-09-22 | G5/G16 clocks через monkeypatch; conflict через `_reload_bound_rules(AccessRules())`; один прогон **161 passed**; TASK-30 не закрыт; runtime не менялся |
