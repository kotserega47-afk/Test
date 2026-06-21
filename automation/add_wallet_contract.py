"""Excel contract and routing for WalletEditor Add Wallet (Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re

import pandas as pd

from automation.audit import normalize_card_digits
from automation.engine import ALLOWED_STATUSES
from core.datetime_utils import EXCEL_DATETIME_FORMAT, now_msk

LOG_PREFIX = "[WalletEditorAdd]"

DEFAULT_STATUS = "Тест"
DEFAULT_STATE = "enabled"
DEFAULT_DIRECTION = "in"
DEFAULT_POOL = "ЧБР"

V1_REQUIRED_COLUMNS = frozenset({"card", "phone"})
PHASE2_OPTIONAL_COLUMNS = frozenset(
    {
        "surname",
        "first_name",
        "patronymic",
        "gender",
        "login",
        "password",
        "password_extra",
        "security_question",
        "bank_card_sim",
        "gateway",
        "balance",
        "topup_method",
        "merch",
        "comment_service",
        "comment_deleted_account",
        "comment",
        "role",
        "marker",
        "cluster_sim",
        "cluster_phone",
        "cluster_sim_slot",
        "server_id",
        "ours",
        "cluster",
        "payout_priority",
        "kyc",
        "queue_length",
        "queue_depth",
        "bakai_customer_id",
    }
)
V1_OPTIONAL_COLUMNS = frozenset(
    {
        "aggregate",
        "aggregates",
        "account",
        "merchant_id_sbp",
        "account_number",
        "partners",
        "groups",
        "status",
        "state",
        "direction",
        "pool",
    }
) | PHASE2_OPTIONAL_COLUMNS
DISABLE_MARKERS = frozenset({"action", "value"})

RESULT_OK = "OK"
RESULT_DRY_RUN = "DRY_RUN_WOULD_CREATE"
RESULT_SKIP_DUP_CARD = "SKIP_DUPLICATE_CARD"
RESULT_SKIP_DUP_FILE = "SKIP_DUPLICATE_IN_FILE"
RESULT_FAIL_INVALID = "FAIL_INVALID_ROW"
RESULT_FAIL_OPEN_MODAL = "FAIL_OPEN_MODAL"
RESULT_FAIL_FILL = "FAIL_FILL_FORM"
RESULT_FAIL_VALIDATION = "FAIL_SAVE_VALIDATION"
RESULT_FAIL_SAVE_TIMEOUT = "FAIL_SAVE_TIMEOUT"
RESULT_FAIL_NOT_FOUND = "FAIL_NOT_FOUND_AFTER_SAVE"
RESULT_FAIL_TECHNICAL = "FAIL_TECHNICAL"

SKIP_RESULTS = frozenset({RESULT_SKIP_DUP_CARD, RESULT_SKIP_DUP_FILE})
FAIL_RESULTS = frozenset(
    {
        RESULT_FAIL_INVALID,
        RESULT_FAIL_OPEN_MODAL,
        RESULT_FAIL_FILL,
        RESULT_FAIL_VALIDATION,
        RESULT_FAIL_SAVE_TIMEOUT,
        RESULT_FAIL_NOT_FOUND,
        RESULT_FAIL_TECHNICAL,
    }
)

_COLUMN_ALIASES = {
    "card": "card",
    "карта": "card",
    "phone": "phone",
    "телефон": "phone",
    "partners": "partners",
    "partner": "partners",
    "groups": "groups",
    "group": "groups",
    "status": "status",
    "статус": "status",
    "state": "state",
    "состояние": "state",
    "direction": "direction",
    "направление": "direction",
    "pool": "pool",
    "пул": "pool",
    "aggregate": "aggregate",
    "агрегат": "aggregate",
    "aggregates": "aggregates",
    "аккаунты": "aggregates",
    "агрегаты": "aggregates",
    "account": "account",
    "аккаунт": "account",
    "merchant_id_sbp": "merchant_id_sbp",
    "merchantid сбп": "merchant_id_sbp",
    "account_number": "account_number",
    "номер счёта": "account_number",
    "номер счета": "account_number",
    "номер расчёта": "account_number",
    "номер расчета": "account_number",
    "surname": "surname",
    "фамилия": "surname",
    "first_name": "first_name",
    "имя": "first_name",
    "patronymic": "patronymic",
    "отчество": "patronymic",
    "gender": "gender",
    "пол": "gender",
    "login": "login",
    "логин": "login",
    "password": "password",
    "пароль": "password",
    "password_extra": "password_extra",
    "дополнительный пароль": "password_extra",
    "security_question": "security_question",
    "контрольный вопрос": "security_question",
    "bank_card_sim": "bank_card_sim",
    "gateway": "gateway",
    "шлюз": "gateway",
    "balance": "balance",
    "баланс": "balance",
    "topup_method": "topup_method",
    "метод пополнения": "topup_method",
    "merch": "merch",
    "comment_service": "comment_service",
    "comment_deleted_account": "comment_deleted_account",
    "comment": "comment",
    "комментарий": "comment",
    "role": "role",
    "роль": "role",
    "marker": "marker",
    "маркер": "marker",
    "cluster_sim": "cluster_sim",
    "cluster_phone": "cluster_phone",
    "cluster_sim_slot": "cluster_sim_slot",
    "server_id": "server_id",
    "ours": "ours",
    "cluster": "cluster",
    "кластер": "cluster",
    "payout_priority": "payout_priority",
    "kyc": "kyc",
    "кус": "kyc",
    "queue_length": "queue_length",
    "queue_depth": "queue_depth",
    "bakai_customer_id": "bakai_customer_id",
    "action": "action",
    "value": "value",
}


class ExcelRouting(str, Enum):
    DISABLE = "disable"
    ADD_WALLET = "add_wallet"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class AddWalletRow:
    row_number: int
    card: str
    phone: str
    status: str = DEFAULT_STATUS
    state: str = DEFAULT_STATE
    direction: str = DEFAULT_DIRECTION
    pool: str = DEFAULT_POOL
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
class PreparedAddWalletRow:
    row: AddWalletRow | None
    result: str
    comment: str


@dataclass
class AddWalletBatchInput:
    rows: list[AddWalletRow]
    invalid_rows: list[PreparedAddWalletRow]
    dry_run: bool = False


@dataclass
class AddWalletRowResult:
    row_number: int
    card: str
    phone: str
    result: str
    comment: str
    processed_at: str
    operator_profile: str
    dry_run: bool
    input_columns: dict[str, str] = field(default_factory=dict)


@dataclass
class AddWalletBatchSummary:
    total: int = 0
    ok: int = 0
    dry_run_would_create: int = 0
    skip: int = 0
    fail: int = 0
    dry_run: bool = False

    def record(self, result: str) -> None:
        self.total += 1
        if result == RESULT_OK:
            self.ok += 1
        elif result == RESULT_DRY_RUN:
            self.dry_run_would_create += 1
        elif result in SKIP_RESULTS:
            self.skip += 1
        elif result in FAIL_RESULTS:
            self.fail += 1

    def telegram_summary(self) -> str:
        return (
            f"[WalletEditorAdd]\n"
            f"Всего строк: {self.total}\n"
            f"OK: {self.ok}\n"
            f"Dry-run would create: {self.dry_run_would_create}\n"
            f"Skip: {self.skip}\n"
            f"Fail: {self.fail}\n"
            f"dry_run: {self.dry_run}"
        )


def normalize_column_name(name: object) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return _COLUMN_ALIASES.get(text, text)


def _read_excel(file_path: str) -> pd.DataFrame:
    df = pd.read_excel(file_path)
    df.columns = [normalize_column_name(c) for c in df.columns]
    return df


def detect_excel_routing(
    file_path: str,
    *,
    original_filename: str | None = None,
) -> tuple[ExcelRouting, str | None]:
    name = (original_filename or Path(file_path).name).strip().lower()
    if name.startswith("add_wallet") and name.endswith(".xlsx"):
        try:
            df = _read_excel(file_path)
        except Exception as exc:
            return ExcelRouting.AMBIGUOUS, f"не удалось прочитать Excel: {exc}"
        cols = set(df.columns)
        if DISABLE_MARKERS & cols:
            return ExcelRouting.AMBIGUOUS, "файл add_wallet* не должен содержать action/value"
        if "card" not in cols or "phone" not in cols:
            return ExcelRouting.AMBIGUOUS, "add_wallet Excel: нужны колонки card и phone"
        return ExcelRouting.ADD_WALLET, None

    try:
        df = _read_excel(file_path)
    except Exception as exc:
        return ExcelRouting.AMBIGUOUS, f"не удалось прочитать Excel: {exc}"

    cols = set(df.columns)
    if DISABLE_MARKERS & cols:
        if "card" not in cols or "action" not in cols:
            return ExcelRouting.AMBIGUOUS, "disable Excel: нужны card и action"
        return ExcelRouting.DISABLE, None

    if "card" in cols and "phone" in cols:
        return ExcelRouting.ADD_WALLET, None

    return ExcelRouting.AMBIGUOUS, (
        "не удалось определить тип файла: нужны card+phone (Add Wallet) "
        "или card+action+value (disable)"
    )


def _cell_str(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def _split_list_field(raw: str) -> str:
    parts = [p.strip() for p in re.split(r"[;,]", raw or "") if p.strip()]
    return ";".join(parts)


def parse_single_aggregate(
    *,
    aggregate: str = "",
    aggregates: str = "",
) -> tuple[str | None, str | None]:
    """Return (single aggregate name, error comment). error is set when aggregates has >1 value."""
    name = (aggregate or "").strip()
    if name:
        return name, None
    parts = [p.strip() for p in re.split(r"[;,]", aggregates or "") if p.strip()]
    if len(parts) > 1:
        return None, f"aggregates: несколько значений ({len(parts)}), допустимо одно"
    if len(parts) == 1:
        return parts[0], None
    return None, None


def _validate_status(value: str) -> str | None:
    if value not in ALLOWED_STATUSES:
        return f"недопустимый status: {value}"
    return None


def _phase2_fields_from_row(row: pd.Series) -> dict[str, str]:
    return {name: _cell_str(row.get(name)) for name in PHASE2_OPTIONAL_COLUMNS}


def prepare_add_wallet_batch(
    file_path: str,
    *,
    dry_run: bool = False,
) -> AddWalletBatchInput:
    df = _read_excel(file_path)
    cols = set(df.columns)

    if DISABLE_MARKERS & cols:
        raise ValueError("файл содержит action/value — это disable contract, не Add Wallet")

    missing = V1_REQUIRED_COLUMNS - cols
    if missing:
        raise ValueError(f"отсутствуют обязательные колонки: {sorted(missing)}")

    seen_cards: dict[str, int] = {}
    runnable: list[AddWalletRow] = []
    invalid: list[PreparedAddWalletRow] = []

    for idx, row in df.iterrows():
        row_number = int(idx) + 2
        card_raw = _cell_str(row.get("card"))
        phone_raw = _cell_str(row.get("phone"))
        card = normalize_card_digits(card_raw)
        phone = normalize_card_digits(phone_raw) or phone_raw.strip()

        input_columns = {
            str(col): _cell_str(row.get(col))
            for col in df.columns
            if col in V1_REQUIRED_COLUMNS | V1_OPTIONAL_COLUMNS
        }

        if not card:
            invalid.append(
                PreparedAddWalletRow(
                    AddWalletRow(
                        row_number=row_number,
                        card="",
                        phone=phone,
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    "пустая или невалидная card",
                )
            )
            continue
        if not phone:
            invalid.append(
                PreparedAddWalletRow(
                    AddWalletRow(
                        row_number=row_number,
                        card=card,
                        phone="",
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    "пустой phone",
                )
            )
            continue

        if card in seen_cards:
            invalid.append(
                PreparedAddWalletRow(
                    AddWalletRow(
                        row_number=row_number,
                        card=card,
                        phone=phone,
                        input_columns=input_columns,
                    ),
                    RESULT_SKIP_DUP_FILE,
                    f"дубликат card в файле (первая строка {seen_cards[card]})",
                )
            )
            continue
        seen_cards[card] = row_number

        status = _cell_str(row.get("status")) or DEFAULT_STATUS
        state = _cell_str(row.get("state")) or DEFAULT_STATE
        direction = _cell_str(row.get("direction")) or DEFAULT_DIRECTION
        pool = _cell_str(row.get("pool")) or DEFAULT_POOL
        partners = _split_list_field(_cell_str(row.get("partners")))
        groups = _split_list_field(_cell_str(row.get("groups")))
        aggregates_raw = _cell_str(row.get("aggregates"))
        aggregate_name, aggregate_error = parse_single_aggregate(
            aggregate=_cell_str(row.get("aggregate")),
            aggregates=aggregates_raw,
        )
        account = _cell_str(row.get("account"))
        merchant_id_sbp = _cell_str(row.get("merchant_id_sbp"))
        account_number = _cell_str(row.get("account_number"))

        if aggregate_error:
            invalid.append(
                PreparedAddWalletRow(
                    AddWalletRow(
                        row_number=row_number,
                        card=card,
                        phone=phone,
                        aggregates=aggregates_raw,
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    aggregate_error,
                )
            )
            continue

        status_error = _validate_status(status)
        if status_error:
            invalid.append(
                PreparedAddWalletRow(
                    AddWalletRow(
                        row_number=row_number,
                        card=card,
                        phone=phone,
                        input_columns=input_columns,
                    ),
                    RESULT_FAIL_INVALID,
                    status_error,
                )
            )
            continue

        runnable.append(
            AddWalletRow(
                row_number=row_number,
                card=card,
                phone=phone,
                status=status,
                state=state,
                direction=direction,
                pool=pool,
                partners=partners,
                groups=groups,
                aggregate=aggregate_name or "",
                account=account,
                merchant_id_sbp=merchant_id_sbp,
                account_number=account_number,
                aggregates=aggregates_raw,
                **_phase2_fields_from_row(row),
                input_columns=input_columns,
            )
        )

    return AddWalletBatchInput(rows=runnable, invalid_rows=invalid, dry_run=dry_run)


def build_result_dataframe(
    results: list[AddWalletRowResult],
) -> pd.DataFrame:
    rows = []
    for item in results:
        base = {
            "row_number": item.row_number,
            "card": item.card,
            "phone": item.phone,
            "result": item.result,
            "comment": item.comment,
            "processed_at": item.processed_at,
            "operator_profile": item.operator_profile,
            "dry_run": item.dry_run,
        }
        for key, value in item.input_columns.items():
            base[f"input_{key}"] = value
        rows.append(base)
    return pd.DataFrame(rows)


def write_result_excel(results: list[AddWalletRowResult], out_path: str) -> str:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_result_dataframe(results).to_excel(out_path, index=False)
    return out_path


def make_row_result(
    row: AddWalletRow | None,
    *,
    row_number: int,
    result: str,
    comment: str,
    operator_profile: str,
    dry_run: bool,
) -> AddWalletRowResult:
    processed_at = now_msk().strftime(EXCEL_DATETIME_FORMAT)
    if row is None:
        return AddWalletRowResult(
            row_number=row_number,
            card="",
            phone="",
            result=result,
            comment=comment,
            processed_at=processed_at,
            operator_profile=operator_profile,
            dry_run=dry_run,
        )
    return AddWalletRowResult(
        row_number=row.row_number if row else row_number,
        card=row.card,
        phone=row.phone,
        result=result,
        comment=comment,
        processed_at=processed_at,
        operator_profile=operator_profile,
        dry_run=dry_run,
        input_columns=dict(row.input_columns),
    )
