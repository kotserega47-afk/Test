# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-34 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-33 close `cecb336da8f01877329d6ad4f0af72d86c42eb20` (review `4682e399…`); [MODULAR_REORG_ANTARES_INGEST_ADMISSION.md](../ops/MODULAR_REORG_ANTARES_INGEST_ADMISSION.md); schedules — TASK-35 |
| **PR** | Draft [#37](https://github.com/deniskotdavydov1991-wq/Test/pull/37) `feat/task-2026-09-17-34-antares-ingest-admission-impl`, base `feat/task-2026-09-17-33-antares-ingest-admission` |
| **Риск** | medium: isolated ingest на общей WE Queue; mixed `add_*_task` не менять |

Review **пройден**. GPT проверил код/diff и тесты на полном SHA `f76f9c96f46b2489ecb08a38c542de421b609ba6`. GPT pytest **не** запускал. Этот docs-коммит — закрытие TASK-34. Ingest admission **реализован**, **не выпущен**. Это **не** drain/join и **не** полный запрет новой работы в процессе. TASK-33 повторно не закрывать. PR #37 остаётся Draft. Merge/deploy нет.

---

## Goal

Isolated Telegram `.xlsx` ingest: early reject + `put_nowait` под admission lock, владение файлом по контракту, mixed без изменений.

---

## Success Criteria

- [x] `WorkAdmission.put_nowait_if_open`: OPEN + реальный `put_nowait` под одним коротким lock; `qsize` вне lock
- [x] `ensure_profile_queue` вне admission lock; Thread.start не бизнес-loop в тестах; stub только `automation.worker.threading`
- [x] Isolated три маршрута; mixed DISABLE в M1; mixed ADD/EDIT — baseline ниже
- [x] I1–I18 и M1; гонки lock put/seal; реальный `Task.cancel()`
- [x] GPT review code/diff/тестов на `f76f9c9…`; pytest GPT не запускал
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

Прежние **20 passed** на `5362a93fcb57af4a436e4a053e4ca3e24ccf85c2` — **не** Event/barrier-гонки. Это последовательные I1–I18/M1 (seal в том же callback, `CancelledError` из side_effect, wrap после `put`).

| Кто | Что |
|-----|-----|
| Cursor | `tests/test_antares_ingest_admission.py` **28 passed**, exit 0, SHA `f76f9c96f46b2489ecb08a38c542de421b609ba6`, Python 3.12.10 |
| Cursor | ingest/compat/registration/inventory + ingest-admission: `tests/test_antares_document_ingest.py tests/unit/test_wallet_editor_tg_integration.py tests/test_behavior_baseline_registration.py tests/test_behavior_baseline_inventory.py tests/test_antares_ingest_admission.py` **95 passed**, exit 0, тот же SHA, 3.12.10 |
| Cursor | admission/handlers/access_guard **123 passed**, исторически на `5362a93fcb57af4a436e4a053e4ca3e24ccf85c2`; runtime после этого SHA не менялся |
| Cursor | lifecycle+boot **52 passed**, исторически на `5362a93…`; runtime не менялся |
| GPT | код/diff и тесты на `f76f9c96f46b2489ecb08a38c542de421b609ba6`; pytest **не** запускал |

Наборы не суммировать. Worker `test_worker_passes_user_output_file_to_registry_schedule` — исторический AttributeError `stage_registry_result_copy`, не этот diff.

### Последовательные сценарии (не гонка lock)

I1–I3 isolated три маршрута; I4 early closed/sealed; I5 allowlist/operator; I6 seal во время download в том же callback; I7 seal после routing в том же callback; I8 wrap после успешного `put`; I9 put падает до элемента; I12 confirmation RuntimeError; I13 частичный download; I14 AMBIGUOUS / `ValueError` routing / ошибка `_route_task`; I15 ensure failure; I16 consumer забрал задачу; I17 OSError `Path.unlink` и helper RuntimeError; I18 ошибка `qsize`; M1 mixed DISABLE; unit `put_nowait_if_open` OPEN/sealed; `ensure_profile_queue` lookup / `Thread.start` error.

### Гонки (Event/barrier + наблюдение lock)

- `test_put_wins_race_seal_waits_on_lock` — реальные `WorkAdmission`/`Queue`; put держит lock на `put_nowait`; seal наблюдается ждущим lock; элемент один, затем SEALED.
- `test_seal_wins_race_put_not_called` — seal держит lock; put наблюдается ждущим; `put_nowait` не вызывается; `AdmissionRejected`.

Мутация (не в коммите): `put_nowait` вынесен из admission lock → `test_put_wins_race_seal_waits_on_lock` **FAILED**; `test_seal_wins_race_put_not_called` остался PASSED. Production восстановлен.

### Реальный `Task.cancel()`

- I10: download записал partial, ждёт Event → `handler.cancel()`; `CancelledError`; файла нет; enqueue нет.
- I11: confirmation после Accepted ждёт Event → `handler.cancel()`; `CancelledError`; enqueue один; файл есть.
- Все прочие asyncio tasks дожидаются; без sleep.

### Mixed ADD/EDIT (M1 не дублирует)

- `tests/test_antares_document_ingest.py::test_edit_wallet_routes_only_to_edit_queue`
- `tests/test_antares_document_ingest.py::test_add_wallet_task_fields_include_dry_run`
- `tests/test_antares_document_ingest.py::test_fallback_disable_queue_fields_and_file_kept`
- `tests/unit/test_wallet_editor_tg_integration.py::test_add_wallet_xlsx_routes_to_add_wallet_task`
- `tests/unit/test_wallet_editor_tg_integration.py::test_handler_uses_profile_queue_size_from_add_task`

---

## Out Of Scope / оставшаяся работа

**Обходы admission:** schedules / `dispatch_job_background` (TASK-35); внутренний `enqueue_auto_enable_batch`; conversion bridge `add_task`; применимые legacy-входы (`automation/tg_receiver.py`).

**Lifecycle:** drain очереди; worker join; sender stop; serve; mixed-stop.

`rules_provider` сам по себе **не** обход постановки WE-задач.

Live Telegram; merge/retarget/deploy; исходное Test.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-22 | isolated ingest admit; Draft PR #37; статус **review (подготовлено)** |
| 2026-09-22 | усиление доказательств: lock-гонки, Task.cancel, Path.unlink OSError, worker fixture namespace |
| 2026-09-22 | GPT review на `f76f9c9…`; закрытие docs; реализован, не выпущен; pytest GPT не запускал |
