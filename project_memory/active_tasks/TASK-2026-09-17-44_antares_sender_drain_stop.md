# Task Workflow v1 — Task

| Мета | Значение |
|------|----------|
| **ID** | TASK-2026-09-17-44 |
| **Статус** | review (подготовлено; merge/deploy не выполнены) |
| **KB версия** | v1.10 |
| **Связанные артефакты** | TASK-43 close `c17eab2fc7962f18b7702be73459afcd9d82133f` (accepted runtime `01e0c55dc84b9e6be78ff20f6b1f5b58be017601`, Draft PR #46); [MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md); [DRAIN_STOP.md](../ops/MODULAR_REORG_ANTARES_DRAIN_STOP.md) S1–S3 / O3 / O10 |
| **PR** | Draft [#47](https://github.com/deniskotdavydov1991-wq/Test/pull/47) `docs/task-2026-09-17-44-antares-sender-drain-stop`, base `feat/task-2026-09-17-43-antares-registry-daemon-join-impl` |
| **Риск** | medium: process-global sender shared with mixed/raccoon callers; ownership gate; S1 vs empty queue |

Docs-only контракт isolated Telegram sender drain/stop ownership. Runtime **не** менялся. Helper не wired. O10 не закрывать. TASK-39–43 повторно не закрывать. Merge/deploy нет.

---

## Goal

Зафиксировать: sender process-global; isolated Antares **не** останавливает его без ownership gate (verdict **B**); S1–S3 + handoff accounting; idle; intake seal после registry join; stop lifecycle; HTTP (отдельный Bot); `send_photo_sync` out-of-queue-scope; матрица SND1–SND15.

---

## Success Criteria

- [x] Обследованы `telegram_bot.py`, `telegram_transport.py`, `application_lifecycle.py` и production callers на `c17eab2…`
- [x] Ownership verdict **B** + refuse без gate; O10 открыт
- [x] S1/S2/S3; handoff ≠ `_record_enqueue`; idle; intake seal; D30
- [x] HTTP lifecycle ptb 22.8; sender Bot ≠ PTB Application Bot
- [x] `send_photo_sync` verdict; SND1–SND15
- [ ] GPT review документа
- [ ] runtime (отдельный code slice после gate/design)
- [ ] merge/deploy (намеренно открыто)

---

## Out Of Scope

runtime sender; executor shutdown; helper/`run_ptb_lifecycle`; serve/polling; mixed-stop; merge/retarget/deploy; live Telegram; повторное закрытие TASK-39–43.

---

## История

| Дата | Событие |
|------|---------|
| 2026-09-24 | docs-контракт sender drain/stop ownership; статус **review (подготовлено)** |
