"""Excel contract and routing for WalletEditor Edit Wallet (v1).

Aggregate column semantics (v1): optional guard only — must match the already-active
aggregate; Edit Wallet never selects or switches aggregate checkboxes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pandas as pd

from automation.add_wallet_contract import (
    DISABLE_MARKERS,
    PHASE2_OPTIONAL_COLUMNS,
    V1_OPTIONAL_COLUMNS,
    _cell_str,
    _split_list_field,
    normalize_column_name,
    parse_single_aggregate,
)
from automation.audit import normalize_card_digits
from automation.engine import ALLOWED_STATUSES
from core.datetime_utils import EXCEL_DATETIME_FORMAT, now_msk

LOG_PREFIX = "[WalletEditorEdit]"

EDIT_UPDATABLE_COLUMNS = frozenset({"phone"}) | V1_OPTIONAL_COLUMNS | PHASE2_OPTIONAL_COLUMNS

CLEAR_WHITELIST_V1 = frozenset(
    {
        "partners",
        "groups",
        "phone",
        "account",
        "merchant_id_sbp",
        "account_number",
        "surname",
        "first_name",
        "patronymic",
        "login",
        "password",
        "password_extra",
        "security_question",
        "bank_card_sim",
        "merch",
        "marker",
        "cluster_sim",
        "cluster_phone",
        "cluster_sim_slot",
        "server_id",
        "bakai_customer_id",
        "comment",
        "comment_service",
        "comment_deleted_account",
        "kyc",
    }
)

CLEAR_BLACKLIST_V1 = frozenset(
    {
        "card",
        "aggregate",
        "aggregates",
        "status",
        "state",
        "direction",
        "pool",
        "gateway",
        "topup_method",
        "role",
        "ours",
        "cluster",
        "payout_priority",
        "balance",
        "queue_length",
        "queue_depth",
        "gender",
    }
)

RESULT_OK = "OK"
RESULT_SKIP_NOT_FOUND = "SKIP_NOT_FOUND"
RESULT_FAIL_INVALID = "FAIL_INVALID_ROW"
RESULT_FAIL_OPEN_CARD = "FAIL_OPEN_CARD"
RESULT_FAIL_FILL = "FAIL_FILL_FORM"
RESULT_FAIL_AGGREGATE_NOT_ACTIVE = "FAIL_AGGREGATE_NOT_ACTIVE"
RESULT_FAIL_VALIDATION = "FAIL_SAVE_VALIDATION"
RESULT_FAIL_SAVE_TIMEOUT = "FAIL_SAVE_TIMEOUT"
RESULT_FAIL_NOT_FOUND = "FAIL_NOT_FOUND_AFTER_SAVE"
RESULT_FAIL_TECHNICAL = "FAIL_TECHNICAL"

FAIL_RESULTS = frozenset(
    {
        RESULT_FAIL_INVALID,
        RESULT_FAIL_OPEN_CARD,
        RESULT_FAIL_FILL,
        RESULT_FAIL_AGGREGATE_NOT_ACTIVE,
        RESULT_FAIL_VALIDATION,
        RESULT_FAIL_SAVE_TIMEOUT,
        RESULT_FAIL_NOT_FOUND,
        RESULT_FAIL_TECHNICAL,
    }
)


class ExcelRouting(str, Enum):
    DISABLE = "disable"
    ADD_WALLET = "add_wallet"
    EDIT_WALLET = "edit_wallet"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class EditWalletRow:
    row_number: int
    card: str
    provided_columns: frozenset[str]
    cleared_columns: frozenset[str] = frozenset()
    phone: str = ""
    status: str = ""
    state: str = ""
    direction: str = ""
    pool: str = ""
    partners: str = ""
    groups: str = ""
    aggregate: str = ""
    account: str = ""
    merchant_id_sbp: str = ""
    account_number: str = ""
    aggregates: str = ""
    surname: str = ""
    first_name: str = ""
    patronymic: str = ""
    gender: str = ""
    login: str = ""
    password: str = ""
    password_extra: str = ""
    security_question: str = ""
    bank_card_sim: str = ""
    gateway: str = ""
    balance: str = ""
    topup_method: str = ""
    merch: str = ""
    comment_service: str = ""
    comment_deleted_account: str = ""
    comment: str = ""
    role: str = ""
    marker: str = ""
    cluster_sim: str = ""
    cluster_phone: str = ""
    cluster_sim_slot: str = ""
    server_id: str = ""
    ours: str = ""
    cluster: str = ""
    payout_priority: str = ""
    kyc: str = ""
    queue_length: str = ""
    queue_depth: str = ""
    bakai_customer_id: str = ""
    input_columns: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedEditWalletRow:
    row: EditWalletRow | None
    result: str
    comment: str


@dataclass
class EditWalletBatchInput:
    rows: list[EditWalletRow]
    invalid_rows: list[PreparedEditWalletRow]


@dataclass
class EditWalletRowResult:
    row_number: int
    card: str
    result: str
    comment: str
    processed_at: str
    operator_profile: str
    input_columns: dict[str, str] = field(default_factory=dict)


@dataclass
class EditWalletBatchSummary:
    total: int = 0
    ok: int = 0
    skip: int = 0
    fail: int = 0

    def record(self, result: str) -> None:
        self.total += 1
        if result == RESULT_OK:
            self.ok += 1
        elif result == RESULT_SKIP_NOT_FOUND:
            self.skip += 1
        elif result in FAIL_RESULTS:
            self.fail += 1

    def telegram_summary(self) -> str:
        return (
            f"[WalletEditorEdit]\n"
            f"Всего строк: {self.total}\n"
            f"OK: {self.ok}\n"
            f"Skip: {self.skip}\n"
            f"Fail: {self.fail}"
        )


def _read_excel(file_path: str) -> pd.DataFrame:
    df = pd.read_excel(file_path)
    df.columns = [normalize_column_name(c) for c in df.columns]
    return df


def _is_edit_wallet_filename(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered.endswith(".xlsx") and "edit_wallet" in lowered


def _validate_status(value: str) -> str | None:
    if value not in ALLOWED_STATUSES:
        return f"недопустимый status: {value}"
    return None


def is_clear_marker(value: object) -> bool:
    return _cell_str(value).upper() == "CLEAR"


def _clear_forbidden_comment(column: str) -> str:
    return f"CLEAR not allowed for column: {column}"


def _column_intents_for_row(
    row: pd.Series,
    columns: set[str],
) -> tuple[frozenset[str], frozenset[str], str | None]:
    provided: set[str] = set()
    cleared: set[str] = set()

    for col in columns:
        if col not in EDIT_UPDATABLE_COLUMNS:
            continue
        raw = _cell_str(row.get(col))
        if not raw:
            continue
        if is_clear_marker(raw):
            if col in CLEAR_BLACKLIST_V1 or col not in CLEAR_WHITELIST_V1:
                return frozenset(), frozenset(), _clear_forbidden_comment(col)
            cleared.add(col)
            continue
        provided.add(col)

    if provided & cleared:
        return frozenset(), frozenset(), "conflicting update and clear intents for the same column"

    return frozenset(provided), frozenset(cleared), None


def build_success_comment(row: EditWalletRow) -> str:
    cleared = ",".join(sorted(row.cleared_columns))
    updated = ",".join(sorted(row.provided_columns))
    return f"card found after save; cleared={cleared}; updated={updated}"


def _phase2_fields_from_row(row: pd.Series) -> dict[str, str]:
    return {name: _cell_str(row.get(name)) for name in PHASE2_OPTIONAL_COLUMNS}


def detect_excel_routing(
    file_path: str,
    *,
    original_filename: str | None = None,
) -> tuple[ExcelRouting, str | None]:
    name = (original_filename or Path(file_path).name).strip()

    if _is_edit_wallet_filename(name):
        try:
            df = _read_excel(file_path)
        except Exception as exc:
            return ExcelRouting.AMBIGUOUS, f"не удалось прочитать Excel: {exc}"
        cols = set(df.columns)
        if DISABLE_MARKERS & cols:
            return ExcelRouting.AMBIGUOUS, "файл edit_wallet* не должен содержать action/value"
        if "card" not in cols:
            return ExcelRouting.AMBIGUOUS, "edit_wallet Excel: нужна колонка card"
        return ExcelRouting.EDIT_WALLET, None

    from automation.add_wallet_contract import detect_excel_routing as detect_legacy

    legacy_routing, error = detect_legacy(file_path, original_filename=original_filename)
    mapping = {
        "disable": ExcelRouting.DISABLE,
        "add_wallet": ExcelRouting.ADD_WALLET,
        "ambiguous": ExcelRouting.AMBIGUOUS,
    }
    return mapping.get(legacy_routing.value, ExcelRouting.AMBIGUOUS), error


def prepare_edit_wallet_batch(file_path: str) -> EditWalletBatchInput:
    df = _read_excel(file_path)
    cols = set(df.columns)

    if DISABLE_MARKERS & cols:
        raise ValueError("файл содержит action/value — это disable contract, не Edit Wallet")

    if "card" not in cols:
        raise ValueError("отсутствует обязательная колонка: card")

    seen_cards: dict[str, int] = {}
    runnable: list[EditWalletRow] = []
    invalid: list[PreparedEditWalletRow] = []

    for idx, row in df.iterrows():
        row_number = int(idx) + 2
        card_raw = _cell_str(row.get("card"))
        card = normalize_card_digits(card_raw)
        provided_columns, cleared_columns, intent_error = _column_intents_for_row(row, cols)

        input_columns = {
            str(col): _cell_str(row.get(col))
            for col in df.columns
            if col == "card" or col in EDIT_UPDATABLE_COLUMNS
        }

        if not card:
            invalid.append(
                PreparedEditWalletRow(
                    EditWalletRow(
                        row_number=row_number,
                        card="",
                        provided_columns=frozenset(),
                        cleared_columns=frozenset(),
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    "пустая или невалидная card",
                )
            )
            continue

        if intent_error:
            invalid.append(
                PreparedEditWalletRow(
                    EditWalletRow(
                        row_number=row_number,
                        card=card,
                        provided_columns=provided_columns,
                        cleared_columns=cleared_columns,
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    intent_error,
                )
            )
            continue

        if not provided_columns and not cleared_columns:
            invalid.append(
                PreparedEditWalletRow(
                    EditWalletRow(
                        row_number=row_number,
                        card=card,
                        provided_columns=frozenset(),
                        cleared_columns=frozenset(),
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    "нет полей для обновления (кроме card)",
                )
            )
            continue

        if card in seen_cards:
            invalid.append(
                PreparedEditWalletRow(
                    EditWalletRow(
                        row_number=row_number,
                        card=card,
                        provided_columns=provided_columns,
                        cleared_columns=cleared_columns,
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    f"дубликат card в файле (первая строка {seen_cards[card]})",
                )
            )
            continue
        seen_cards[card] = row_number

        aggregate_raw = _cell_str(row.get("aggregate"))
        aggregates_raw = _cell_str(row.get("aggregates"))
        if not is_clear_marker(aggregate_raw):
            aggregate_name, aggregate_error = parse_single_aggregate(
                aggregate=aggregate_raw,
                aggregates=aggregates_raw if not is_clear_marker(aggregates_raw) else "",
            )
        else:
            aggregate_name, aggregate_error = None, None
        if aggregate_error:
            invalid.append(
                PreparedEditWalletRow(
                    EditWalletRow(
                        row_number=row_number,
                        card=card,
                        provided_columns=provided_columns,
                        cleared_columns=cleared_columns,
                        aggregates=aggregates_raw,
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    aggregate_error,
                )
            )
            continue

        status = _cell_str(row.get("status"))
        if "status" in provided_columns:
            status_error = _validate_status(status)
            if status_error:
                invalid.append(
                    PreparedEditWalletRow(
                        EditWalletRow(
                            row_number=row_number,
                            card=card,
                            provided_columns=provided_columns,
                            cleared_columns=cleared_columns,
                            input_columns=input_columns,
                        ),
                        RESULT_FAIL_INVALID,
                        status_error,
                    )
                )
                continue

        partners_raw = _cell_str(row.get("partners"))
        groups_raw = _cell_str(row.get("groups"))
        partners = (
            ""
            if "partners" in cleared_columns
            else _split_list_field(partners_raw)
        )
        groups = (
            ""
            if "groups" in cleared_columns
            else _split_list_field(groups_raw)
        )

        phase2 = _phase2_fields_from_row(row)
        runnable.append(
            EditWalletRow(
                row_number=row_number,
                card=card,
                provided_columns=provided_columns,
                cleared_columns=cleared_columns,
                phone=_cell_str(row.get("phone")),
                status=status,
                state=_cell_str(row.get("state")),
                direction=_cell_str(row.get("direction")),
                pool=_cell_str(row.get("pool")),
                partners=partners,
                groups=groups,
                aggregate=aggregate_name or "",
                account=_cell_str(row.get("account")),
                merchant_id_sbp=_cell_str(row.get("merchant_id_sbp")),
                account_number=_cell_str(row.get("account_number")),
                aggregates=aggregates_raw,
                **phase2,
                input_columns=input_columns,
            )
        )

    return EditWalletBatchInput(rows=runnable, invalid_rows=invalid)


def build_result_dataframe(results: list[EditWalletRowResult]) -> pd.DataFrame:
    rows = []
    for item in results:
        base = {
            "row_number": item.row_number,
            "card": item.card,
            "result": item.result,
            "comment": item.comment,
            "processed_at": item.processed_at,
            "operator_profile": item.operator_profile,
        }
        for key, value in item.input_columns.items():
            base[f"input_{key}"] = value
        rows.append(base)
    return pd.DataFrame(rows)


def write_result_excel(results: list[EditWalletRowResult], out_path: str) -> str:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_result_dataframe(results).to_excel(out_path, index=False)
    return out_path


def make_row_result(
    row: EditWalletRow | None,
    *,
    row_number: int,
    result: str,
    comment: str,
    operator_profile: str,
) -> EditWalletRowResult:
    processed_at = now_msk().strftime(EXCEL_DATETIME_FORMAT)
    if row is None:
        return EditWalletRowResult(
            row_number=row_number,
            card="",
            result=result,
            comment=comment,
            processed_at=processed_at,
            operator_profile=operator_profile,
        )
    return EditWalletRowResult(
        row_number=row.row_number if row else row_number,
        card=row.card,
        result=result,
        comment=comment,
        processed_at=processed_at,
        operator_profile=operator_profile,
        input_columns=dict(row.input_columns),
    )
