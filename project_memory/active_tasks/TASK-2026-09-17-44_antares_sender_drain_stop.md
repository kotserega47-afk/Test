# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-44 |
| **Статус** | review (пройден; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-43 close `c17eab2fc7962f18b7702be73459afcd9d82133f` (accepted runtime `01e0c55dc84b9e6be78ff20f6b1f5b58be017601`, Draft PR #46); [MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) S1–S3 / O3 / O10 |
| **PR** | Draft [#47](https://github.com/deniskotdavydov1991-wq/Test/pull/47) `docs/task-2026-09-17-44-antares-sender-drain-stop`, base `feat/task-2026-09-17-43-antares-registry-daemon-join-impl` |
| **Риск** | medium: process-global sender shared with mixed/raccoon callers; ownership gate; S1 vs empty queue; PTB `>=20.7` vs surveyed 22.8 |

Review **пройден**. GPT проверил PR #47 / контракт / карточку / diff на полном SHA `0e770a38eac570585ba698b30a39ef7162fe10b5`. Runtime changes отсутствуют (только `project_memory/**`). GPT pytest **не** запускал / not applicable (docs-only). Этот docs-коммит — закрытие TASK-44.

Контракт isolated Telegram sender drain/stop ownership **принят**, **не выпущен**. Runtime implementation — отдельная будущая задача (не стартовать автоматически). PR #47 остаётся Draft. Merge/deploy/retarget/helper wiring нет. TASK-39–43 повторно не закрывать.

**Открытые dependencies (в docs-close не выбирать):**

1. exact shape of dedicated Antares sender ownership gate;
2. PTB version contract / compatibility gate — pin supported version(s) **или** version-tolerant fail-closed implementation.

---

## Goal

Зафиксировать: sender process-global; isolated Antares **не** останавливает его без ownership gate (verdict **B**); S1–S3 + handoff accounting; idle; intake seal после registry join; stop lifecycle; HTTP full Bot request graph + PTB version gate; exact-once `task_done`; `send_photo_sync` out-of-queue-scope; матрица SND1–SND15.

---

## Success Criteria

- [x] Обследованы `telegram_bot.py`, `telegram_transport.py`, `application_lifecycle.py` и production callers на `c17eab2…`
- [x] Ownership verdict **B** + refuse без gate; O10 открыт
- [x] S1/S2/S3; handoff ≠ `_record_enqueue`; idle; intake seal; D30
- [x] HTTP: public vs private API; full request graph; PTB `>=20.7` compatibility gate; sender Bot ≠ PTB Application Bot
- [x] Exact-once `task_done` in `finally`; SND14 усилен; D27/D28 delivery ≠ resource shutdown
- [x] `send_photo_sync` verdict; SND1–SND15
- [x] GPT review документа на `0e770a3…`; pytest GPT не запускал (docs-only)
- [ ] runtime (отдельная future задача; не этот close)
- [ ] ownership gate shape / PTB pin-or-fail-closed (открытые dependencies)
- [ ] merge/deploy (намеренно открыто)

---

## Происхождение проверки

| Кто | Что |
|-----|-----|
| Cursor | docs-only на base `c17eab2…`; runtime нет |
| GPT | review PR #47 / контракт / diff на `0e770a3…`; pytest **не** запускал / not applicable |

---

## Out Of Scope

runtime sender; `requirements.txt` / PTB pin; executor shutdown; helper/`run_ptb_lifecycle`; serve/polling; mixed-stop; merge/retarget/deploy; live Telegram; повторное закрытие TASK-39–43; молчаливый выбор ownership gate / PTB pin.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | docs-контракт sender drain/stop ownership; статус **review (подготовлено)** |
| 2026-09-24 | review blockers: PTB version gate; full Bot request graph; exact-once `task_done`; D27/D28 vs resource |
| 2026-09-24 | GPT review `0e770a3…`; закрытие docs; runtime нет; gate/PTB dependencies открыты |
