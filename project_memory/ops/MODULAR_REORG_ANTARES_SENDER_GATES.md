# Контракт Antares sender ownership gate + PTB compatibility gate (TASK-45)

| Мета | Значение |
|------|----------|
| **Статус** | docs-контракт **подготовлен** (этот срез); runtime **нет**; merge/deploy нет |
| **База** | закрытие TASK-44 `49193bb0d5353d3c528b5a6a382cf5ac22ceb718` (accepted review `0e770a38eac570585ba698b30a39ef7162fe10b5`, Draft PR #47) |
| **Обследованный SHA** | `49193bb…` |
| **Sender drain** | [MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md](MODULAR_REORG_ANTARES_SENDER_DRAIN_STOP.md) TASK-44 — ownership **B** + S1–S3; **открытые** gate dependencies **закрываются здесь** |
| **Drain/stop** | [MODULAR_REORG_ANTARES_DRAIN_STOP.md](MODULAR_REORG_ANTARES_DRAIN_STOP.md) O3 / O10 (**O10 не закрывать**) |
| **Mixed gate** | [TASK-2026-09-17-03](../active_tasks/TASK-2026-09-17-03_early_profile_gate.md) — **не** ослаблять |

Цель: **закрыть** две design-dependency TASK-44 до sender runtime:

1. точная форма dedicated Antares ownership proof;
2. точная стратегия PTB version / compatibility gate.

После TASK-45 будущий code slice **не** выбирает эти решения сам. Runtime в этом PR **нет**.

---

## 0. Жёсткие правила (preflight)

1. **Preflight-before-mutation:** ownership valid? ∧ PTB compatibility valid? ∧ required sender handles/state available? — **все PASS** до любой destructive mutation (intake seal, S1 mutation, sentinel, HTTP close, `loop.stop`).
2. Failure любого mandatory preflight → **REFUSE**, zero mutation; shared sender остаётся usable.
3. `WorkAdmission.seal()` **никогда** не ownership proof.
4. Голое чтение mutable `PROJECT_PROFILE` в момент shutdown **не** единственное доказательство dedicated ownership.
5. O10 mixed-stop **не** закрывается.
6. Helper / `run_ptb_lifecycle` / executor shutdown / `requirements.txt` — **не** этот срез.

Порядок preflight (принят):

```
1. ownership proof
2. PTB compatibility (capabilities + request graph intelligibility)
3. sender structural state / handles
4. только потом → intake seal → drain → worker/HTTP/loop/thread
```

---

## 1. Survey: boot / profile / sender import (SHA `49193bb…`)

### 1.1 `enforce_antares_isolated_profile`

| Факт | Доказательство |
|------|----------------|
| Единственный production caller | `apps/antares.py` → `_boot_prefix()` **первая** операция |
| Требует | explicit `PROJECT_PROFILE=antares` (`decide_antares_isolated_boot`); unset/empty → `IsolatedAntaresProfileError` |
| Возвращает | строку `"antares"`; **не** создаёт process-local attestation object |
| Читает env | в момент вызова (`project_profile_env_value`) — **mutable** после boot |
| До Telegram/jobs | да: до `load_dotenv`, `assemble_antares`, Application build |
| Token gate | сразу после enforce: пустой `TELEGRAM_BOT_TOKEN` → `_fail(...)` — successfully booted dedicated Antares **ожидает** token / sender Bot |

### 1.2 Legacy mixed `scheduler.py`

| Факт | Доказательство |
|------|----------------|
| Gate | `enforce_legacy_scheduler_profile()` **до** Telegram/jobs imports |
| Unset/`""` | `legacy_mixed` → продолжение |
| Explicit `PROJECT_PROFILE=antares` | **refuse** (`UnwiredProjectProfileError`) — mixed scheduler **не** стартует с antares profile |
| Затем | `from integrations.telegram_bot import log_telegram_health_if_due` → поднимает global sender |

### 1.3 `parse_project_profile`

Unset/empty → `name="antares"` с `implicit_default=True`. Это **parse default**, **не** доказательство isolated Antares process (`project_profile.py` docstring).

### 1.4 Sender import без profile gate

`integrations.telegram_bot` импортируется из WE/registry/routes/jobs/analyzers/downloader/`main.py`/… **без** обязательного `enforce_*`. Любой процесс, импортировавший модуль, получает global loop/queue/worker.

### 1.5 Process-local attestation сегодня

**Отсутствует.** Нет owner object / claim / token.

### 1.6 PTB

`requirements.txt`: `python-telegram-bot>=20.7`. Survey env 22.8 — observation only (TASK-44).

---

## 2. Ownership gate — сравнение вариантов

| | Доказывает | Не доказывает | Mixed spoof | Mutable env | Import-order | Repeat | Testability | Spoof in-process | Helper |
|--|------------|---------------|-------------|-------------|--------------|--------|-------------|------------------|--------|
| **A** read `PROJECT_PROFILE` at stop | текущее env | boot path; dedicated entry | unset mixed + later set antares; implicit parse confusion | **да** | n/a | easy | easy | env mutate | weak |
| **B** re-call `enforce_antares_isolated_profile` at stop | env currently exact antares | process был dedicated с boot; claim immutable | same as A if env flipped | **да** | n/a | easy | easy | env mutate | weak |
| **C** process-local claim after boot enforce | entry прошёл isolated gate once | сам по себе не передаёт proof в API | mixed **не** создаёт claim | claim immutable | claim **до** assemble/sender import | yes | inject claim in tests | only via claim API | needs pass-through |
| **D** explicit proof into stop API | caller holds proof | кто создал proof | без C можно подставить fake | n/a | n/a | yes | mock proof | need identity vs process claim | natural |

### 2.1 Отвергнуто

- **A alone:** G5 (env flip) ломает; seal≠ownership уже TASK-44; implicit `antares` name опасен.
- **B alone:** тот же mutable-env риск; не доказывает boot entrypoint; повторный enforce ≠ immutable process fact.
- **D alone:** без C нет единственного trusted creator; любой caller мог бы сфабриковать token без process claim.

### 2.2 ПРИНЯТО: **C + D**

**Модель:**

```
apps.antares / _boot_prefix
  → enforce_antares_isolated_profile() SUCCESS
  → create process-local immutable AntaresSenderOwnershipAttestation (CLAIMED)
  → (только после claim) assemble / later sender import
  → lifecycle owner держит/передаёт ту же attestation
  → stop_isolated_sender(ownership_proof=…)
       проверяет proof is process claim (identity / exact attestation)
  → mixed/default entrypoint attestation НЕ получает → REFUSE
```

Совместимость с import/boot order: `enforce` уже **до** `assemble_antares`; handlers могут позже lazy-import `telegram_bot`. Claim **обязан** создаваться **сразу после** успешного enforce и **до** assemble/`Application` — чтобы attestation существовала до первого sender import на Antares path.

---

## 3. Принятый ownership contract (закрыто)

### 3.1 Что является proof

Process-local **immutable** attestation object (точные Python-имена — code slice), создаваемый **только** designated claim API, вызываемым из isolated Antares entry (`apps.antares` boot path) после успешного `enforce_antares_isolated_profile`.

Proof = reference/identity этой attestation (передаётся в stop API). Не env string. Не `WorkAdmission`.

### 3.2 Owner roles

| Роль | Кто |
|------|-----|
| **Creator** | Isolated Antares entrypoint (`apps.antares` `_boot_prefix` after enforce) |
| **Holder / passer** | Future lifecycle owner / stop orchestrator (helper later — **не** wire сейчас) |
| **Validator** | Future sender stop preflight |

### 3.3 Lifecycle

Conceptual:

`UNCLAIMED` → (successful Antares enforce + claim) → `CLAIMED_ANTARES` **immutable until process exit**.

Принято для первого isolated cutover:

- **нет** release/reassign другому владельцу в том же process;
- Raccoon/mixed **не** claim’ит тот же process sender после Antares claim;
- после successful sender stop claim **остаётся** CLAIMED (repeat stop same proof = idempotent success — § 6);
- mixed process: остаётся UNCLAIMED → stop REFUSE.

### 3.4 Validation rules

1. Missing proof → REFUSE before mutation (G2).
2. Mixed/legacy / UNCLAIMED → REFUSE; sender usable (G3).
3. Sealed admission alone → REFUSE (G4).
4. Env set to `antares` after boot without claim → REFUSE (G5).
5. Same valid proof repeatedly → idempotent PASS (G6).
6. Conflicting/foreign proof vs process claim → REFUSE, no mutation (G7).
7. Lost/invalid claim state → fail-closed REFUSE.
8. Refuse **до** intake seal / S1 mutation / sentinel / HTTP / loop.stop.

### 3.5 Process-level

Ownership proof — **process-level**, не per-message / per-admission.

---

## 4. PTB compatibility — сравнение и выбор

| Strategy | Плюсы | Минусы |
|----------|-------|--------|
| **A** pin exact/supported range в repo | воспроизводимость | TASK-45 **не** меняет `requirements.txt`; pin alone без capability check всё равно хрупок across patch |
| **B** runtime capability gate + fail-closed | работает с `>=20.7`; не требует pin в этом срезе | нужен точный capability list |
| **A+B** pin + capability | максимум | pin — отдельный ops/PR; не блокер correctness stop |

### 4.1 ПРИНЯТО: **B — runtime capability gate + version-tolerant fail-closed**

- Repo floor остаётся `python-telegram-bot>=20.7` (не менять в TASK-45).
- Surveyed **22.8** = observation only.
- **Не** полагаться на `version >= X` как единственную проверку.
- Exact pin в `requirements.txt` — **опциональный** follow-up ops (не открытый вопрос correctness; не требуется этим контрактом для sender stop).

Вопрос TASK-44 «pin или compatibility?» **закрыт:** compatibility capability gate обязателен; pin не выбран как обязательный path.

### 4.2 Public vs private

**PUBLIC (mandatory для PASS, после проверки наличия на runtime):**

- callable `Bot.shutdown` (async);
- на каждом owned request object: callable `shutdown` (`BaseRequest` / `HTTPXRequest`).

**PRIVATE / diagnostic only (не стабильный API `>=20.7`→∞):**

- `Bot._request`, `Bot._requests_initialized`;
- `request._client` / `is_closed`.

Private допустимы для discovery/leftover diagnostics; **не** единственный способ объявить PASS без public shutdown.

### 4.3 Full request graph preflight (vs resource close)

**PRE-FLIGHT (до mutation):** доказать, что полный owned request graph sender Bot **понятен и закрываем**:

- graph available / unambiguous (semantics как `_iter_bot_requests`: все request objects этого Bot);
- module-level `integrations.telegram_bot.request` **не** считается автоматически единственным;
- для каждого owned request: public `shutdown` available **или** fail-closed;
- можно диагностировать leftover после close.

Если graph unavailable / ambiguous / required public shutdown missing / **sender Bot is None** → **`ptb_compatibility_unsupported`** или **`sender_bot_unavailable`** (точное Python-имя — code slice) → REFUSE **до** seal/stop/HTTP/loop.

**Нельзя:** закрыть «что нашли» и объявить success без понятного full graph.

**RESOURCE CLOSE (после PASS preflight + drain/worker stop):** best-effort close **каждого** owned request независимо; failure одного → per-request leftover в result; **не** путать с preflight failure (G12).

### 4.4 Capability checklist (PASS requires all)

1. Sender `Bot` object **MUST** be present for claimed isolated Antares stop. `bot is None` / expected Bot unavailable → **fail-closed** preflight (**не** no-op success). Production: `apps.antares._boot_prefix` already `_fail`s without `TELEGRAM_BOT_TOKEN`; successfully claimed stop therefore expects a live module `bot`.
2. `Bot.shutdown` public callable present.
3. Full owned request graph enumerable and non-ambiguous.
4. Each request in graph has public `shutdown` callable.
5. Shutdown can be scheduled/awaited on **sender** event loop (structural: loop handle available — part of structural preflight § 5).

Missing any → refuse before mutation: no intake seal, no S1 mutation, no worker stop, no HTTP touch, no `loop.stop`, no shutdown thread join.

Code slice **не** выбирает NO_TOKEN no-op policy — решение закрыто здесь.

---

## 5. Structural preflight (handles)

После ownership + PTB capability PASS, до mutation проверить наличие required future handles (TASK-44): loop thread handle, worker Task handle, lifecycle state. Missing → REFUSE before mutation (не partial stop).

---

## 6. Future stop API semantics (имена — code slice)

Conceptual:

```
stop_isolated_sender(ownership_proof, *, timeout=None) -> …
```

Обязательные свойства:

| Свойство | Правило |
|----------|---------|
| Proof required | нет proof → REFUSE |
| No implicit proof | admission / env / re-enforce ≠ proof |
| Preflight first | ownership → PTB → structural; all PASS else REFUSE |
| No mutation on refuse | intake/S1/sentinel/HTTP/loop untouched |
| Repeat + same owner + already stopped | **idempotent success** |
| Repeat + same owner + still running | continue/complete stop per drain contract |
| Foreign / no proof when already stopped | **REFUSE** (не «success»); no further mutation |
| Conflicting claim attempt at create | explicit failure; no replace |
| Shared/mixed after refuse | sender remains usable |

---

## 7. Independence of gates

| Failure | Mutation? |
|---------|-----------|
| Ownership FAIL | нет |
| Ownership PASS, PTB FAIL | нет |
| Both PASS, structural FAIL | нет |
| All PASS | mutation phase allowed (G15) |

---

## 8. Матрица G1–G15 (будущий code; без sleep-as-proof)

| ID | Сценарий | Ожидание |
|----|----------|----------|
| G1 | valid dedicated Antares claim/proof | ownership preflight **PASS** |
| G2 | no proof | **REFUSE** before intake seal |
| G3 | mixed/legacy UNCLAIMED | **REFUSE**; sender usable |
| G4 | WorkAdmission sealed, proof absent | **REFUSE** |
| G5 | env → `PROJECT_PROFILE=antares` after boot, no claim | **REFUSE** |
| G6 | same valid proof repeatedly | idempotent **PASS** |
| G7 | conflicting/foreign proof | **REFUSE**, no mutation |
| G8 | ownership PASS, PTB FAIL | no seal / no worker stop / no HTTP close |
| G9 | Bot absent (`bot is None` / expected Bot unavailable) **или** mandatory public `Bot.shutdown` / request `shutdown` missing | fail-closed preflight; **no mutation** |
| G10 | Bot request graph unavailable/ambiguous | fail-closed preflight |
| G11 | capable Bot present + intelligible full graph | compatibility **PASS** |
| G12 | preflight PASS; one request fails during actual close | per-request leftover; ≠ preflight fail |
| G13 | already stopped + same owner | idempotent success |
| G14 | already stopped + foreign/no proof | **REFUSE** (explicit); no mutation |
| G15 | all gates PASS | mutation phase **allowed** |

---

## 9. Связь с TASK-44

TASK-44 verdict **B** (shared sender; refuse без proof) **сохранён**. Этот документ **закрывает** open questions ownership shape и PTB strategy. S1–S3 / handoff / idle / D30 / photo / SND* **не** переписываются.

Helper: при ownership/PTB refuse **не** заявлять полный graceful (TASK-44 open Q5 остаётся operational; не wire).

---

## 10. Out of scope TASK-45

Sender accounting/seal/sentinel/worker/HTTP/loop/thread; ownership/PTB **runtime**; `requirements.txt` change; executor shutdown; `run_ptb_lifecycle`; serve/polling; mixed-stop (O10); deploy; live Telegram; merge/retarget; повторное закрытие TASK-39–44; автостарт sender code.
