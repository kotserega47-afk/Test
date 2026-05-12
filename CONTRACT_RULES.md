# CONTRACT OF RULES — Design Specification

**Status:** design spec (aligned with **CONTRACT V2**).  
**Normative document (sections 1–22 in full):** [`CONTRACT_V2.md`](CONTRACT_V2.md)

Историческое имя **CONTRACT_RULES** сохраняется как точка входа для ссылок «контракт правил». Вся целевая нормативная детализация workbook, нормализации, валидации, precedence, инвариантов, миграции и тестов — в **`CONTRACT_V2.md`**.

---

## Краткое содержание (см. CONTRACT_V2.md)

| § | Раздел |
|---|--------|
| 1 | [Purpose](CONTRACT_V2.md#1-purpose) |
| 2 | [Versioning](CONTRACT_V2.md#2-versioning) |
| 3 | [Excel workbook schema](CONTRACT_V2.md#3-excel-workbook-schema) |
| 4 | [Entity identity contract](CONTRACT_V2.md#4-entity-identity-contract) |
| 5 | [Normalization contract](CONTRACT_V2.md#5-normalization-contract) |
| 6 | [Layer responsibility model](CONTRACT_V2.md#6-layer-responsibility-model) |
| 7 | [Validation policy](CONTRACT_V2.md#7-validation-policy) |
| 8 | [Precedence rules](CONTRACT_V2.md#8-precedence-rules) |
| 9 | [Runtime invariants](CONTRACT_V2.md#9-runtime-invariants) |
| 10 | [Failure modes](CONTRACT_V2.md#10-failure-modes) |
| 11 | [Migration compatibility](CONTRACT_V2.md#11-migration-compatibility) |
| 12 | [Version migration policy](CONTRACT_V2.md#12-version-migration-policy) |
| 13 | [Deterministic ordering rule](CONTRACT_V2.md#13-deterministic-ordering-rule) |
| 14 | [Runtime guarantees](CONTRACT_V2.md#14-runtime-guarantees) |
| 15 | [Test requirements](CONTRACT_V2.md#15-test-requirements) |
| 16 | [Implementation constraints](CONTRACT_V2.md#16-implementation-constraints) |
| 17 | [Snapshot lifecycle model](CONTRACT_V2.md#17-snapshot-lifecycle-model) |
| 18 | [Structured validation and error model](CONTRACT_V2.md#18-structured-validation-and-error-model) |
| 19 | [Runtime and contract compatibility matrix](CONTRACT_V2.md#19-runtime-and-contract-compatibility-matrix) |
| 20 | [Performance and execution guarantees](CONTRACT_V2.md#20-performance-and-execution-guarantees) |
| 21 | [Source of truth](CONTRACT_V2.md#21-source-of-truth) |
| 22 | [Stage roadmap](CONTRACT_V2.md#22-stage-roadmap) |

Якоря соответствуют заголовкам `## N. ...` в `CONTRACT_V2.md` (рендер GitHub / VS Code).

---

## Связь с версией Excel

- В листе **`meta`** ключ **`version`** — версия **контракта workbook** (см. CONTRACT_V2 §2, §12, §19).
- Текущие продуктовые файлы могут иметь `version = 3`; целевой контракт V2 описан в **`CONTRACT_V2.md`** и при внедрении потребует согласованного bump `meta.version` и кода валидатора.

---

## Changelog (история до объединения в V2 design spec)

**v3 — 12.02.2026** (предыдущая живая спецификация в этом файле)

- `wallet_limits`: добавлена обязательная колонка `analyzers` (multi-analyzer support).
- `wallet_limits` применяются к конкретному `ANALYZER_KEY`.

**2026-05-11**

- Введён **`CONTRACT_V2.md`** как полный design spec; **`CONTRACT_RULES.md`** — входная точка + оглавление.
- Pass 2: в `CONTRACT_V2.md` добавлены §4 Entity identity, §6 Layer responsibility, §12 Version migration, §13 Deterministic ordering, §14 Runtime guarantees, §16 Implementation constraints; полная перенумерация §1–§16; обновлено оглавление в `CONTRACT_RULES.md`.
- Pass 3: §17–§22 (snapshot lifecycle, error codes, compatibility matrix, performance, source of truth, stage roadmap); оглавление §1–§22.

**2026-05-12**

- Stage 1 / C2: §18 в `CONTRACT_V2.md` дополнен тремя кодами workbook-schema валидации — `RULE_MISSING_SHEET`, `RULE_MISSING_COLUMN`, `RULE_DEPRECATED_COLUMN`. Runtime semantics не меняется; коды используются только новым модулем `core/rules_v2/validation_workbook.py` (lib-only, без подключения к CLI до C6).
