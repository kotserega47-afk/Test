"""Wallet Editor action ``set_aggregate`` — switch aggregate checkbox and nested fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from automation.add_wallet_engine import SaveWaitOutcome
    from automation.runtime import RunConfig

from automation.audit import log, log_timing, mask_card, normalize_card_digits
import re
import time

RESULT_OK_SET_AGGREGATE = "OK_SET_AGGREGATE"
RESULT_STOP_BEFORE_SAVE = "STOP_BEFORE_SAVE"
RESULT_DRY_RUN_WOULD_SET_AGGREGATE = "DRY_RUN_WOULD_SET_AGGREGATE"
RESULT_SKIP_NOT_FOUND = "SKIP_NOT_FOUND"
RESULT_FAIL_AGGREGATE_NOT_FOUND = "FAIL_AGGREGATE_NOT_FOUND"
RESULT_FAIL_AGGREGATE_SWITCH = "FAIL_AGGREGATE_SWITCH"
RESULT_FAIL_AGGREGATE_FIELDS = "FAIL_AGGREGATE_FIELDS"
RESULT_FAIL_SAVE_TIMEOUT = "FAIL_SAVE_TIMEOUT"
RESULT_FAIL_VALIDATION = "FAIL_VALIDATION"
RESULT_FAIL_VERIFY = "FAIL_VERIFY"
RESULT_FAIL_OPEN_CARD = "FAIL_OPEN_CARD"
RESULT_FAIL_TECHNICAL = "FAIL_TECHNICAL"
RESULT_FAIL_SET_AGGREGATE_CONFLICT = "FAIL_SET_AGGREGATE_CONFLICT"

SET_AGGREGATE_RESULT_CODES = frozenset(
    {
        RESULT_OK_SET_AGGREGATE,
        RESULT_STOP_BEFORE_SAVE,
        RESULT_DRY_RUN_WOULD_SET_AGGREGATE,
        RESULT_SKIP_NOT_FOUND,
        RESULT_FAIL_AGGREGATE_NOT_FOUND,
        RESULT_FAIL_AGGREGATE_SWITCH,
        RESULT_FAIL_AGGREGATE_FIELDS,
        RESULT_FAIL_SAVE_TIMEOUT,
        RESULT_FAIL_VALIDATION,
        RESULT_FAIL_VERIFY,
        RESULT_FAIL_OPEN_CARD,
        RESULT_FAIL_TECHNICAL,
        RESULT_FAIL_SET_AGGREGATE_CONFLICT,
    }
)

# Excel optional columns (canonical). Presence of the column controls whether we touch the field.
SET_AGGREGATE_OPTIONAL_COLUMNS = (
    "phone",
    "account",
    "merchant_id_sbp",
    "account_number",
)

_OPTIONAL_COLUMN_ALIASES: dict[str, str] = {
    "phone": "phone",
    "телефон": "phone",
    "account": "account",
    "аккаунт": "account",
    "merchant_id_sbp": "merchant_id_sbp",
    "merchantid сбп": "merchant_id_sbp",
    "merchantid_sbp": "merchant_id_sbp",
    "account_number": "account_number",
    "номер счёта": "account_number",
    "номер счета": "account_number",
    "номер расчёта": "account_number",
    "номер расчета": "account_number",
}

# Antares checkbox/DOM settle after aggregate toggles (UI updates asynchronously).
_AGGREGATE_TOGGLE_TIMEOUT_MS = 8_000
_AGGREGATE_TOGGLE_POLL_MS = 75
_AGGREGATE_UNCHECK_ROUNDS = 12
_AGGREGATE_FIELDS_WAIT_MS = 5_000
_NESTED_FIELD_POLL_MS = 50
_NESTED_FIELD_WAIT_MS = 5_000
_SET_AGGREGATE_TIMING_PROFILE = "wallet_editor"
_SET_AGGREGATE_TIMING_SCOPE = "set_aggregate"

# One DOM evaluate: only real aggregate checkboxes inside .custom-control.custom-checkbox.
_AGGREGATE_SNAPSHOT_JS = r"""
(root) => {
  const out = [];
  const seen = new Set();
  const boxes = root.querySelectorAll('.custom-control.custom-checkbox');
  for (const box of boxes) {
    const input = box.querySelector('input[type="checkbox"]');
    if (!input) continue;
    const id = (input.id || '').trim();
    if (!id) continue;
    let label = null;
    try {
      label = root.querySelector('label[for="' + CSS.escape(id) + '"]');
    } catch (e) {
      label = null;
    }
    if (!label) {
      const labels = root.querySelectorAll('label[for]');
      for (const candidate of labels) {
        if ((candidate.getAttribute('for') || '') === id) {
          label = candidate;
          break;
        }
      }
    }
    if (!label) {
      label = box.querySelector('label');
    }
    if (!label) continue;
    const raw = (label.innerText || label.textContent || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    if (!raw) continue;
    const key = raw.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ name: raw, checked: !!input.checked, id: id });
  }
  return out;
}
"""

# Nested field labels that indicate aggregate expansion (Sim A / ЧБР / etc.).
_SET_AGGREGATE_EXPANSION_LABELS = (
    "Аккаунт",
    "MerchantId СБП",
    "Девайс",
    "Карта",
    "Номер слота",
    "Номер счёта",
    "Номер счета",
    "Номер расчёта",
    "Номер расчета",
)


class AggregateSwitchError(RuntimeError):
    """Raised when aggregate checkbox switch cannot be confirmed within timeout."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AggregateCheckboxSnapshot:
    """One real aggregate checkbox from a single DOM snapshot."""

    name: str
    checked: bool
    id: str


def _time_set_aggregate_step(step: str, *, card: str | None = None):
    """Context manager for safe WE/timing logs (no full card/phone values)."""

    class _Timer:
        def __enter__(self):
            self._started = time.perf_counter()
            self.outcome = "ok"
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is not None:
                self.outcome = "fail"
            duration_ms = round((time.perf_counter() - self._started) * 1000)
            log_timing(
                profile=_SET_AGGREGATE_TIMING_PROFILE,
                scope=_SET_AGGREGATE_TIMING_SCOPE,
                step=step,
                duration_ms=duration_ms,
                outcome=self.outcome,
                card=card,
            )
            return False

    return _Timer()


def _mask_phone(value: object) -> str:
    digits = "".join(c for c in str(value or "") if c.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def _mask_secret_field(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) >= 6:
        return _mask_phone(digits)
    if len(text) <= 4:
        return "***"
    return f"***{text[-4:]}"


def _normalize_aggregate_label_text(text: str) -> str:
    """Collapse whitespace from label.inner_text (may include nested <span>)."""
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def _css_escape_ident(value: str) -> str:
    """Minimal CSS ident escape for getElementById-style selectors."""
    # Prefer attribute selector over #id when id has special chars.
    return value.replace("\\", "\\\\").replace('"', '\\"')


def aggregate_checkbox_for_label(modal, label_el):
    """Resolve checkbox strictly — never via neighboring preceding/following inputs.

    Allowed bindings only:
    1) ``label[for]`` → ``input[type=checkbox][id=...]`` inside the same modal;
    2) ``input[type=checkbox]`` inside the nearest ``.custom-control.custom-checkbox``.
    """
    try:
        for_id = label_el.get_attribute("for")
    except Exception:
        for_id = None

    if for_id:
        for_id = str(for_id).strip()
        if for_id:
            escaped = _css_escape_ident(for_id)
            checkbox = modal.locator(
                f'input[type="checkbox"][id="{escaped}"]'
            )
            try:
                if checkbox.count() > 0:
                    return checkbox.first
            except Exception:
                pass
            # for= points elsewhere (text input etc.) — not an aggregate checkbox.
            return None

    # Fallback: same custom-checkbox container only (no form-check wandering, no XPath neighbors).
    try:
        container = label_el.locator(
            "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), "
            "' custom-checkbox ')][1]"
        )
        if container.count() == 0:
            return None
        checkbox = container.locator('input[type="checkbox"]')
        if checkbox.count() == 0:
            return None
        return checkbox.first
    except Exception:
        return None


def aggregate_label_matches(text: str, aggregate_name: str) -> bool:
    left = _normalize_aggregate_label_text(text).casefold()
    right = _normalize_aggregate_label_text(aggregate_name).casefold()
    return bool(left) and left == right


def find_aggregate_checkbox_label(modal, aggregate_name: str):
    """Find label whose normalized text matches and has a strict checkbox binding."""
    labels = modal.locator("label")
    try:
        count = labels.count()
    except Exception:
        return None
    for i in range(count):
        label_el = labels.nth(i)
        try:
            text = _normalize_aggregate_label_text(
                label_el.inner_text(timeout=500)
            )
        except Exception:
            continue
        if not aggregate_label_matches(text, aggregate_name):
            continue
        if aggregate_checkbox_for_label(modal, label_el) is not None:
            return label_el
    return None


def _aw():
    """Lazy import Add Wallet helpers (avoids engine ↔ add_wallet circular import)."""
    import automation.add_wallet_engine as aw

    return aw


@dataclass(frozen=True)
class SetAggregateIntent:
    """One Excel row intent for set_aggregate.

    Optional fields use ``None`` when the column is absent from Excel (do not touch).
    Empty string means the column is present but the cell is empty (still do not fill/clear).
    Non-empty string means fill (and later verify) that nested field.
    """

    card: str
    aggregate: str
    phone: str | None = None
    account: str | None = None
    merchant_id_sbp: str | None = None
    account_number: str | None = None

    def provided_optional(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in SET_AGGREGATE_OPTIONAL_COLUMNS:
            value = getattr(self, key)
            if value is None:
                continue
            text = normalize_set_aggregate_optional_cell(value)
            if text:
                out[key] = text
        return out


def normalize_set_aggregate_column_name(name: object) -> str | None:
    text = str(name or "").strip().casefold().replace("\u00a0", " ")
    text = " ".join(text.split())
    return _OPTIONAL_COLUMN_ALIASES.get(text)


def present_set_aggregate_optional_columns(columns: object) -> frozenset[str]:
    present: set[str] = set()
    for col in columns or ():
        canonical = normalize_set_aggregate_column_name(col)
        if canonical:
            present.add(canonical)
    return frozenset(present)


def normalize_set_aggregate_optional_cell(raw: object) -> str:
    """Normalize optional Excel cell for phone/account/merchant_id_sbp/account_number.

    Integer-valued Excel/pandas numerics (``float`` / ``int`` / numpy scalars) become
    digit strings without a trailing ``.0``. Plain text is left unchanged — no global
    ``.0`` string stripping. Empty / NaN → ``""``.
    """
    if raw is None:
        return ""
    try:
        import pandas as pd

        # pd.isna handles float nan, pd.NA, NaT; avoid bool arrays from list-likes.
        if not isinstance(raw, (list, tuple, dict)) and pd.isna(raw):
            return ""
    except Exception:
        pass

    # bool is a numbers.Integral subclass — keep textual form.
    if isinstance(raw, bool):
        return str(raw)

    import math
    import numbers

    if isinstance(raw, numbers.Integral):
        return str(int(raw))

    if isinstance(raw, numbers.Real):
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            text = str(raw).strip()
            return "" if not text or text.lower() == "nan" else text
        if not math.isfinite(number):
            return ""
        if number.is_integer():
            return str(int(number))
        # Keep meaningful fractional part (do not strip ".0" from arbitrary text).
        text = str(raw).strip()
        return "" if not text or text.lower() == "nan" else text

    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return ""
    return text


def _cell_optional(raw: object) -> str:
    return normalize_set_aggregate_optional_cell(raw)


def intent_from_excel_row(
    *,
    card: str,
    aggregate: str,
    row: object,
    present_optional: frozenset[str],
    columns: object | None = None,
) -> SetAggregateIntent:
    """Build intent; ``None`` optional fields mean column absent."""
    col_map: dict[str, str] = {}
    if columns is None:
        columns = getattr(row, "index", ())
    try:
        col_iter = list(columns)
    except TypeError:
        col_iter = []
    for col in col_iter:
        canonical = normalize_set_aggregate_column_name(col)
        if canonical and canonical not in col_map:
            col_map[canonical] = str(col)

    def _opt(canonical: str) -> str | None:
        if canonical not in present_optional:
            return None
        actual = col_map.get(canonical, canonical)
        try:
            raw = row[actual]  # type: ignore[index]
        except Exception:
            try:
                raw = getattr(row, actual)
            except Exception:
                raw = ""
        return _cell_optional(raw)

    return SetAggregateIntent(
        card=str(card).strip(),
        aggregate=str(aggregate).strip(),
        phone=_opt("phone"),
        account=_opt("account"),
        merchant_id_sbp=_opt("merchant_id_sbp"),
        account_number=_opt("account_number"),
    )


def _read_input_value(field) -> str:
    try:
        return str(field.input_value() or "").strip()
    except Exception:
        try:
            return str(field.evaluate("el => el.value") or "").strip()
        except Exception:
            return ""


def read_top_level_phone(page: "Page") -> str:
    """Main wallet phone (first «Телефон» input) — never the nested aggregate phone."""
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)
    matches = aw._find_all_text_inputs_by_label_in(modal, "Телефон")
    if not matches:
        field = aw._find_text_input_by_label_in(modal, "Телефон")
        if field is None:
            return ""
        return _read_input_value(field)
    return _read_input_value(matches[0])


def read_nested_field(page: "Page", labels: tuple[str, ...]) -> str | None:
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)
    for label in labels:
        field = aw._find_aggregate_field_input(modal, label)
        if field is None:
            continue
        try:
            if not field.is_visible():
                continue
        except Exception:
            pass
        return _read_input_value(field)
    return None


def parse_aggregate_snapshot_raw(
    raw: object,
) -> list[AggregateCheckboxSnapshot]:
    """Normalize evaluate() payload into AggregateCheckboxSnapshot list."""
    aw = _aw()
    out: list[AggregateCheckboxSnapshot] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _normalize_aggregate_label_text(str(item.get("name") or ""))
        checkbox_id = str(item.get("id") or "").strip()
        if not name or not checkbox_id:
            continue
        if aw._is_kyc_label_text(name):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            AggregateCheckboxSnapshot(
                name=name,
                checked=bool(item.get("checked")),
                id=checkbox_id,
            )
        )
    return out


def snapshot_aggregate_checkboxes(
    page: "Page", *, card: str | None = None
) -> list[AggregateCheckboxSnapshot]:
    """One DOM evaluate → real aggregate checkboxes only (no field captions)."""
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)
    with _time_set_aggregate_step("aggregate_snapshot", card=card):
        try:
            raw = modal.evaluate(_AGGREGATE_SNAPSHOT_JS)
        except Exception as exc:
            log.warning("⚠️ [SetAggregate] aggregate snapshot failed: %s", exc)
            return []
        return parse_aggregate_snapshot_raw(raw)


def list_aggregate_checkbox_states(page: "Page") -> list[tuple[str, bool]]:
    """Fresh snapshot of real aggregate checkboxes only.

    Field captions such as «Бакай» / «Номер слота» / «Телефон» that are not bound
    via ``.custom-control.custom-checkbox`` + checkbox ``id``/``label[for]`` are
    excluded. Never uses preceding/following XPath neighbors.
    """
    return [(e.name, e.checked) for e in snapshot_aggregate_checkboxes(page)]


def find_aggregate_state(page: "Page", aggregate_name: str) -> bool | None:
    """Return checked state for exact aggregate label, or None if not found."""
    for entry in snapshot_aggregate_checkboxes(page):
        if aggregate_label_matches(entry.name, aggregate_name):
            return entry.checked
    return None


def active_aggregate_names(page: "Page") -> list[str]:
    return [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]


def target_is_sole_active(page: "Page", aggregate_name: str) -> bool:
    active = [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]
    if len(active) != 1:
        return False
    return aggregate_label_matches(active[0], aggregate_name)


def _snapshot_entry(
    snap: list[AggregateCheckboxSnapshot], aggregate_name: str
) -> AggregateCheckboxSnapshot | None:
    for entry in snap:
        if aggregate_label_matches(entry.name, aggregate_name):
            return entry
    return None


def _checkbox_locator_by_id(page: "Page", checkbox_id: str):
    """Fresh checkbox locator by id — never reuse stale handles across toggles."""
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)
    escaped = _css_escape_ident(checkbox_id)
    checkbox = modal.locator(f'input[type="checkbox"][id="{escaped}"]')
    try:
        if checkbox.count() == 0:
            return None
    except Exception:
        return None
    return checkbox.first


def _fresh_aggregate_checkbox(page: "Page", aggregate_name: str):
    """Resolve aggregate checkbox via latest DOM snapshot id (DOM may recreate)."""
    entry = _snapshot_entry(snapshot_aggregate_checkboxes(page), aggregate_name)
    if entry is None:
        return None
    return _checkbox_locator_by_id(page, entry.id)


def _nested_field_visible(page: "Page", labels: tuple[str, ...]) -> bool:
    """True when a nested aggregate control for the label is visible.

    For «Карта» / «Телефон» require a duplicate input (top-level + nested). A single
    match is the wallet-level field and must not be treated as aggregate-internal.
    """
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)
    for label in labels:
        if label in {"Карта", "Телефон"}:
            matches = aw._find_all_text_inputs_by_label_in(modal, label)
            if len(matches) < 2:
                continue
            try:
                if matches[-1].is_visible():
                    return True
            except Exception:
                return True
            continue
        field = aw._find_aggregate_field_input(modal, label)
        if field is None:
            continue
        try:
            if field.is_visible():
                return True
        except Exception:
            return True
    return False


def detect_set_aggregate_expansion(page: "Page") -> str | None:
    """Detect nested aggregate block (Sim A / ЧБР / etc.) without false top-level hits."""
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)

    for label in _SET_AGGREGATE_EXPANSION_LABELS:
        if label in {"Карта", "Телефон"}:
            continue
        field = aw._find_text_input_by_label_in(modal, label)
        if field is None:
            continue
        try:
            if field.is_visible():
                return label
        except Exception:
            return label

    # Duplicate nested «Карта» / «Телефон» (top-level + aggregate).
    for label in ("Карта", "Телефон"):
        matches = aw._find_all_text_inputs_by_label_in(modal, label)
        if len(matches) >= 2:
            try:
                if matches[-1].is_visible():
                    return label
            except Exception:
                return label
    return None


def wait_for_set_aggregate_fields_visible(
    page: "Page", aggregate_name: str, *, card: str | None = None
) -> None:
    """Legacy expansion wait (Device/etc.) — prefer ``wait_for_requested_nested_fields``."""
    with _time_set_aggregate_step("aggregate_fields_wait", card=card):
        deadline = time.monotonic() + _AGGREGATE_FIELDS_WAIT_MS / 1000.0
        while time.monotonic() < deadline:
            detected = detect_set_aggregate_expansion(page)
            if detected:
                log.info(
                    "[SetAggregate] nested_fields_visible aggregate=%s via=%s",
                    aggregate_name,
                    detected,
                )
                return
            page.wait_for_timeout(_AGGREGATE_TOGGLE_POLL_MS)
        raise RuntimeError(f"aggregate fields not visible: {aggregate_name}")


# One DOM pass: all visible text inputs grouped by normalized label.
_LIST_LABELED_INPUTS_JS = r"""
(root) => {
  const norm = (s) => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const out = [];
  const rows = root.querySelectorAll('div.row');
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const lab = row.querySelector('label');
    if (!lab) continue;
    const label = norm(lab.innerText || lab.textContent || '');
    if (!label) continue;
    const input = row.querySelector(
      "input[type='text'], input[type='password'], " +
      "input:not([type='checkbox']):not([type='radio']):not([type='hidden']):not([type='submit']):not([type='button'])"
    );
    if (!input) continue;
    const rects = input.getClientRects();
    const visible = !!(rects && rects.length > 0);
    out.push({
      label: label,
      id: (input.id || '').trim(),
      value: (input.value || '').trim(),
      visible: visible,
      disabled: !!input.disabled,
      readOnly: !!input.readOnly,
      rowIndex: i,
    });
  }
  return out;
}
"""


def _list_labeled_inputs(page: "Page") -> list[dict]:
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)
    try:
        raw = modal.evaluate(_LIST_LABELED_INPUTS_JS)
    except Exception as exc:
        log.warning("⚠️ [SetAggregate] labeled inputs evaluate failed: %s", exc)
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _inputs_for_labels(
    labeled: list[dict], labels: tuple[str, ...]
) -> list[dict]:
    wanted = {_normalize_aggregate_label_text(x).casefold() for x in labels}
    out: list[dict] = []
    for item in labeled:
        name = _normalize_aggregate_label_text(str(item.get("label") or ""))
        if name.casefold() in wanted:
            out.append(item)
    return out


def _locator_for_labeled_input(page: "Page", item: dict):
    """Fresh Playwright locator for a labeled-input snapshot entry."""
    aw = _aw()
    modal = page.locator(aw.MODAL_BODY)
    checkbox_id = str(item.get("id") or "").strip()
    if checkbox_id:
        escaped = _css_escape_ident(checkbox_id)
        loc = modal.locator(f'input[id="{escaped}"]')
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass
    # Fallback: Nth matching label row input (rowIndex from evaluate).
    try:
        row_index = int(item.get("rowIndex"))
    except Exception:
        return None
    row = modal.locator("div.row").nth(row_index)
    inp = row.locator(
        "input[type='text'], input[type='password'], "
        "input:not([type='checkbox']):not([type='radio']):not([type='hidden'])"
    )
    try:
        if inp.count() == 0:
            return None
    except Exception:
        return None
    return inp.first


def _pick_nested_phone_item(
    phone_items: list[dict], *, expected_top_phone: str
) -> dict | None:
    """Choose nested phone input; never the wallet top-level phone."""
    visible = [
        p
        for p in phone_items
        if p.get("visible") and not p.get("disabled") and not p.get("readOnly")
    ]
    if len(visible) < 2:
        return None

    top_digits = normalize_card_digits(expected_top_phone)
    top_idx = 0
    if top_digits:
        for i, item in enumerate(visible):
            if normalize_card_digits(str(item.get("value") or "")) == top_digits:
                top_idx = i
                break

    for i, item in enumerate(visible):
        if i == top_idx:
            continue
        return item
    # Ambiguous: only top matched and nothing else left.
    return None


def _pick_nested_generic_item(items: list[dict]) -> dict | None:
    visible = [
        p
        for p in items
        if p.get("visible") and not p.get("disabled") and not p.get("readOnly")
    ]
    if not visible:
        return None
    # For Телефон/Карта duplicates the nested control is the last one.
    return visible[-1]


_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "phone": ("Телефон",),
    "account": ("Аккаунт",),
    "merchant_id_sbp": ("MerchantId СБП",),
    "account_number": (
        "Номер счёта",
        "Номер счета",
        "Номер расчёта",
        "Номер расчета",
    ),
}


def wait_for_requested_nested_fields(
    page: "Page",
    intent: SetAggregateIntent,
    *,
    expected_top_phone: str,
) -> dict[str, object]:
    """Wait only for Excel-provided nested fields; return ready locators.

    Does not wait for Device / nested Card. Phone wait excludes the top-level
    wallet phone. Raises RuntimeError on timeout → FAIL_AGGREGATE_FIELDS.
    """
    provided = intent.provided_optional()
    if not provided:
        log.info(
            "[SetAggregate] nested_fields_skip reason=no_optional_fields aggregate=%s",
            intent.aggregate,
        )
        return {}

    needed = set(provided.keys())
    found: dict[str, object] = {}
    deadline = time.monotonic() + _NESTED_FIELD_WAIT_MS / 1000.0
    phone_search_count = 0
    phone_wait_started: float | None = None

    while time.monotonic() < deadline:
        labeled = _list_labeled_inputs(page)

        if "phone" in needed and "phone" not in found:
            if phone_wait_started is None:
                phone_wait_started = time.perf_counter()
            phone_search_count += 1
            phones = _inputs_for_labels(labeled, _FIELD_LABELS["phone"])
            item = _pick_nested_phone_item(
                phones, expected_top_phone=expected_top_phone
            )
            if item is not None:
                loc = _locator_for_labeled_input(page, item)
                if loc is not None:
                    found["phone"] = loc
                    duration_ms = round(
                        (time.perf_counter() - phone_wait_started) * 1000
                    )
                    log_timing(
                        profile=_SET_AGGREGATE_TIMING_PROFILE,
                        scope=_SET_AGGREGATE_TIMING_SCOPE,
                        step="nested_phone_wait",
                        duration_ms=duration_ms,
                        outcome="ok",
                        card=intent.card,
                    )
                    log.info(
                        "[SetAggregate] nested_phone_found searches=%s "
                        "wait_ms=%s top_phone=%s",
                        phone_search_count,
                        duration_ms,
                        _mask_phone(expected_top_phone),
                    )

        for key in ("account", "merchant_id_sbp", "account_number"):
            if key not in needed or key in found:
                continue
            items = _inputs_for_labels(labeled, _FIELD_LABELS[key])
            item = _pick_nested_generic_item(items)
            if item is None and len(items) == 1 and items[0].get("visible"):
                item = items[0]
            if item is not None:
                loc = _locator_for_labeled_input(page, item)
                if loc is not None:
                    found[key] = loc

        if needed <= set(found.keys()):
            return found

        page.wait_for_timeout(_NESTED_FIELD_POLL_MS)

    if phone_wait_started is not None and "phone" not in found:
        log_timing(
            profile=_SET_AGGREGATE_TIMING_PROFILE,
            scope=_SET_AGGREGATE_TIMING_SCOPE,
            step="nested_phone_wait",
            duration_ms=round((time.perf_counter() - phone_wait_started) * 1000),
            outcome="fail",
            card=intent.card,
        )

    missing = sorted(needed - set(found.keys()))
    raise RuntimeError(
        f"requested nested fields not visible: {missing} "
        f"aggregate={intent.aggregate}"
    )


def _read_locator_value(field) -> str:
    try:
        return str(field.input_value() or "").strip()
    except Exception:
        try:
            return str(field.evaluate("el => el.value") or "").strip()
        except Exception:
            return ""


def _fill_locator_confirmed(
    page: "Page",
    *,
    field,
    value: str,
    label: str,
    resolve_fresh,
    card: str | None = None,
    timing_prefix: str = "nested_phone",
) -> None:
    """Fill a known locator; confirm value; one fresh-locator retry if needed."""
    aw = _aw()
    current = field
    attempts = 0
    with _time_set_aggregate_step(f"{timing_prefix}_fill", card=card):
        for attempt in range(1, 3):
            attempts = attempt
            try:
                aw._fill_locator_text(current, label, value)
            except Exception as exc:
                if attempt >= 2:
                    raise
                log.info(
                    "[SetAggregate] %s_fill_error attempt=%s err=%s — retry fresh",
                    timing_prefix,
                    attempt,
                    type(exc).__name__,
                )
                current = resolve_fresh()
                if current is None:
                    raise
                continue

            with _time_set_aggregate_step(
                f"{timing_prefix}_confirm", card=card
            ):
                actual = _read_locator_value(current)
                if _values_match(value, actual):
                    log.info(
                        "[SetAggregate] %s_fill_confirmed attempts=%s value=%s",
                        timing_prefix,
                        attempts,
                        _mask_secret_field(value)
                        if timing_prefix != "nested_phone"
                        else _mask_phone(value),
                    )
                    return

            log.info(
                "[SetAggregate] %s_fill_mismatch attempt=%s — retry fresh locator",
                timing_prefix,
                attempt,
            )
            current = resolve_fresh()
            if current is None:
                break

    raise RuntimeError(f"failed to set nested field {label}")


def fill_requested_nested_fields(
    page: "Page",
    intent: SetAggregateIntent,
    fields: dict[str, object],
    *,
    expected_top_phone: str,
) -> None:
    """Fill locators returned by wait — no second full label scan."""
    provided = intent.provided_optional()

    def resolve_phone():
        labeled = _list_labeled_inputs(page)
        phones = _inputs_for_labels(labeled, _FIELD_LABELS["phone"])
        item = _pick_nested_phone_item(
            phones, expected_top_phone=expected_top_phone
        )
        return _locator_for_labeled_input(page, item) if item else None

    if "phone" in provided:
        field = fields.get("phone")
        if field is None:
            raise RuntimeError("nested phone locator missing after wait")
        log.info(
            "[SetAggregate] fill_start nested_phone=%s (top-level phone untouched)",
            _mask_phone(provided["phone"]),
        )
        _fill_locator_confirmed(
            page,
            field=field,
            value=provided["phone"],
            label="Телефон",
            resolve_fresh=resolve_phone,
            card=intent.card,
            timing_prefix="nested_phone",
        )
        log.info(
            "[SetAggregate] fill_done field=phone value=%s",
            _mask_phone(provided["phone"]),
        )

    for key, labels in (
        ("account", ("Аккаунт",)),
        ("merchant_id_sbp", ("MerchantId СБП",)),
        ("account_number", _FIELD_LABELS["account_number"]),
    ):
        if key not in provided:
            continue
        field = fields.get(key)
        if field is None:
            raise RuntimeError(f"nested {key} locator missing after wait")

        def resolve_key(k=key, labs=labels):
            labeled = _list_labeled_inputs(page)
            items = _inputs_for_labels(labeled, labs)
            item = _pick_nested_generic_item(items)
            if item is None and len(items) == 1:
                item = items[0]
            return _locator_for_labeled_input(page, item) if item else None

        log.info(
            "[SetAggregate] fill_start field=%s value=%s",
            key,
            _mask_secret_field(provided[key]),
        )
        _fill_locator_confirmed(
            page,
            field=field,
            value=provided[key],
            label=labels[0],
            resolve_fresh=resolve_key,
            card=intent.card,
            timing_prefix=f"nested_{key}",
        )
        log.info("[SetAggregate] fill_done field=%s", key)


def fill_set_aggregate_nested_fields_from_locators(
    page: "Page",
    intent: SetAggregateIntent,
    fields: dict[str, object],
    *,
    expected_top_phone: str,
) -> None:
    """Fill using locators from wait — no Device wait, no second full label scan."""
    with _time_set_aggregate_step("aggregate_fill", card=intent.card):
        # Nested «Карта» is not an Excel optional column — never wait for it.
        # One-shot opportunistic fill only if already present (e.g. ЧБР).
        labeled = _list_labeled_inputs(page)
        cards = _inputs_for_labels(labeled, ("Карта",))
        visible_cards = [c for c in cards if c.get("visible")]
        if len(visible_cards) >= 2:
            loc = _locator_for_labeled_input(page, visible_cards[-1])
            if loc is not None:
                log.info(
                    "[SetAggregate] fill_start nested_card=%s",
                    mask_card(intent.card),
                )
                try:
                    _aw()._fill_locator_text(loc, "Карта", intent.card)
                    log.info(
                        "[SetAggregate] fill_done field=card value=%s",
                        mask_card(intent.card),
                    )
                except Exception as exc:
                    log.info(
                        "[SetAggregate] fill_skip field=card reason=%s",
                        type(exc).__name__,
                    )
        else:
            log.info(
                "[SetAggregate] fill_skip field=card reason=not_present_for_aggregate "
                "aggregate=%s search_card=%s",
                intent.aggregate,
                mask_card(intent.card),
            )

        fill_requested_nested_fields(
            page, intent, fields, expected_top_phone=expected_top_phone
        )


def fill_set_aggregate_nested_fields(page: "Page", intent: SetAggregateIntent) -> None:
    """Compatibility wrapper: wait requested fields then fill their locators."""
    top_phone = read_top_level_phone(page)
    fields = wait_for_requested_nested_fields(
        page, intent, expected_top_phone=top_phone
    )
    fill_set_aggregate_nested_fields_from_locators(
        page, intent, fields, expected_top_phone=top_phone
    )


def _wait_aggregate_checked_state(
    page: "Page",
    aggregate_name: str,
    *,
    want_checked: bool,
    timeout_ms: int = _AGGREGATE_TOGGLE_TIMEOUT_MS,
) -> bool:
    """Poll snapshots until the named aggregate reaches the desired checked state."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_state: bool | None = None
    while time.monotonic() < deadline:
        snap = snapshot_aggregate_checkboxes(page)
        entry = _snapshot_entry(snap, aggregate_name)
        if entry is not None:
            last_state = entry.checked
            if entry.checked is want_checked:
                return True
        page.wait_for_timeout(_AGGREGATE_TOGGLE_POLL_MS)
    log.info(
        "[SetAggregate] wait_checked_timeout aggregate=%s want_checked=%s last_state=%s",
        aggregate_name,
        want_checked,
        last_state,
    )
    return False


def _wait_target_sole_active(
    page: "Page",
    aggregate_name: str,
    *,
    timeout_ms: int = _AGGREGATE_TOGGLE_TIMEOUT_MS,
    card: str | None = None,
) -> bool:
    """Poll snapshots until target is the only checked aggregate."""
    with _time_set_aggregate_step("aggregate_switch_sole_active", card=card):
        deadline = time.monotonic() + timeout_ms / 1000.0
        last_active: list[str] = []
        while time.monotonic() < deadline:
            snap = snapshot_aggregate_checkboxes(page)
            last_active = [e.name for e in snap if e.checked]
            if len(last_active) == 1 and aggregate_label_matches(
                last_active[0], aggregate_name
            ):
                log.info(
                    "[SetAggregate] sole_active_confirmed aggregate=%s active=%s",
                    aggregate_name,
                    last_active,
                )
                return True
            page.wait_for_timeout(_AGGREGATE_TOGGLE_POLL_MS)
        log.info(
            "[SetAggregate] sole_active_timeout aggregate=%s active=%s",
            aggregate_name,
            last_active,
        )
        return False


def switch_to_single_aggregate(
    page: "Page", aggregate_name: str, *, card: str | None = None
) -> None:
    """Uncheck other aggregates and check the target using fast DOM snapshots."""
    snap = snapshot_aggregate_checkboxes(page, card=card)
    before_active = [e.name for e in snap if e.checked]
    log.info(
        "[SetAggregate] switch_start target=%s active_before=%s",
        aggregate_name,
        before_active,
    )

    if _snapshot_entry(snap, aggregate_name) is None:
        raise AggregateSwitchError(f"aggregate checkbox not found: {aggregate_name}")

    if (
        len(before_active) == 1
        and aggregate_label_matches(before_active[0], aggregate_name)
    ):
        log.info(
            "[SetAggregate] aggregate already sole active aggregate=%s — skip toggles",
            aggregate_name,
        )
        return

    # Uncheck every other checked aggregate (fresh snapshot + locator each time).
    for round_idx in range(_AGGREGATE_UNCHECK_ROUNDS):
        snap = snapshot_aggregate_checkboxes(page, card=card)
        others = [
            e
            for e in snap
            if e.checked and not aggregate_label_matches(e.name, aggregate_name)
        ]
        if not others:
            log.info(
                "[SetAggregate] no_other_active_left target=%s round=%s",
                aggregate_name,
                round_idx,
            )
            break

        other = others[0]
        log.info(
            "[SetAggregate] uncheck_start aggregate=%s remaining_others=%s",
            other.name,
            [e.name for e in others],
        )
        with _time_set_aggregate_step("aggregate_switch_uncheck", card=card):
            checkbox = _checkbox_locator_by_id(page, other.id)
            if checkbox is None:
                # DOM recreated — take a fresh snapshot id and retry once.
                snap = snapshot_aggregate_checkboxes(page, card=card)
                refreshed = _snapshot_entry(snap, other.name)
                if refreshed is None:
                    raise AggregateSwitchError(
                        f"aggregate checkbox lost during switch: {other.name}"
                    )
                checkbox = _checkbox_locator_by_id(page, refreshed.id)
                if checkbox is None:
                    raise AggregateSwitchError(
                        f"aggregate checkbox lost during switch: {other.name}"
                    )
            try:
                checkbox.uncheck(force=True)
            except Exception as exc:
                log.info(
                    "[SetAggregate] uncheck_click_error aggregate=%s err=%s — will re-query",
                    other.name,
                    type(exc).__name__,
                )

            if not _wait_aggregate_checked_state(
                page, other.name, want_checked=False
            ):
                raise AggregateSwitchError(
                    f"timeout waiting unchecked aggregate={other.name} "
                    f"active={[e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]}"
                )
        log.info(
            "[SetAggregate] uncheck_confirmed aggregate=%s active_now=%s",
            other.name,
            [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked],
        )
    else:
        raise AggregateSwitchError(
            f"failed to uncheck other aggregates target={aggregate_name} "
            f"active={[e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]}"
        )

    # Ensure target is checked (fresh snapshot id + poll).
    log.info("[SetAggregate] check_start aggregate=%s", aggregate_name)
    with _time_set_aggregate_step("aggregate_switch_check", card=card):
        snap = snapshot_aggregate_checkboxes(page, card=card)
        target = _snapshot_entry(snap, aggregate_name)
        if target is None:
            raise AggregateSwitchError(
                f"aggregate checkbox not found after uncheck: {aggregate_name}"
            )
        checkbox = _checkbox_locator_by_id(page, target.id)
        if checkbox is None:
            raise AggregateSwitchError(
                f"aggregate checkbox lost before check: {aggregate_name}"
            )
        try:
            already = bool(checkbox.is_checked())
        except Exception:
            already = False
            snap = snapshot_aggregate_checkboxes(page, card=card)
            target = _snapshot_entry(snap, aggregate_name)
            if target is None:
                raise AggregateSwitchError(
                    f"aggregate checkbox lost before check: {aggregate_name}"
                )
            checkbox = _checkbox_locator_by_id(page, target.id)
            if checkbox is None:
                raise AggregateSwitchError(
                    f"aggregate checkbox lost before check: {aggregate_name}"
                )

        if not already:
            try:
                checkbox.check(force=True)
            except Exception as exc:
                log.info(
                    "[SetAggregate] check_click_error aggregate=%s err=%s — will re-query",
                    aggregate_name,
                    type(exc).__name__,
                )
                snap = snapshot_aggregate_checkboxes(page, card=card)
                target = _snapshot_entry(snap, aggregate_name)
                if target is None:
                    raise AggregateSwitchError(
                        f"aggregate checkbox lost on check retry: {aggregate_name}"
                    ) from exc
                checkbox = _checkbox_locator_by_id(page, target.id)
                if checkbox is None:
                    raise AggregateSwitchError(
                        f"aggregate checkbox lost on check retry: {aggregate_name}"
                    ) from exc
                checkbox.check(force=True)
            log.info("[SetAggregate] check_clicked aggregate=%s", aggregate_name)
        else:
            log.info("[SetAggregate] check_already_on aggregate=%s", aggregate_name)

        if not _wait_aggregate_checked_state(
            page, aggregate_name, want_checked=True
        ):
            raise AggregateSwitchError(
                f"timeout waiting checked aggregate={aggregate_name} "
                f"active={[e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]}"
            )
    log.info(
        "[SetAggregate] check_confirmed aggregate=%s active_now=%s",
        aggregate_name,
        [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked],
    )

    if not _wait_target_sole_active(page, aggregate_name, card=card):
        active = [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]
        raise AggregateSwitchError(
            f"timeout waiting sole active expected={aggregate_name!r} active={active!r}"
        )


def _values_match(expected: str, actual: str | None) -> bool:
    if actual is None:
        return False
    exp = str(expected).strip()
    act = str(actual).strip()
    if exp == act:
        return True
    exp_d = normalize_card_digits(exp)
    act_d = normalize_card_digits(act)
    if exp_d and act_d and exp_d == act_d:
        return True
    return False


_VERIFY_FIELDS_JS = r"""
(root, wanted) => {
  const norm = (s) => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const labelOf = (el) => {
    const id = el.id;
    if (id) {
      try {
        const byFor = root.querySelector('label[for="' + CSS.escape(id) + '"]');
        if (byFor) return norm(byFor.innerText || byFor.textContent || '');
      } catch (e) {}
    }
    const wrap = el.closest('.form-group, .form-row, .row, .custom-control, div');
    if (wrap) {
      const lab = wrap.querySelector('label');
      if (lab) return norm(lab.innerText || lab.textContent || '');
    }
    return '';
  };
  const byLabel = {};
  const inputs = root.querySelectorAll('input[type="text"], input:not([type]), textarea');
  for (const input of inputs) {
    const label = labelOf(input);
    if (!label) continue;
    if (!byLabel[label]) byLabel[label] = [];
    byLabel[label].push((input.value || '').trim());
  }
  const pick = (names, nestedLast) => {
    for (const name of names) {
      const vals = byLabel[name];
      if (!vals || !vals.length) continue;
      return nestedLast && vals.length >= 2 ? vals[vals.length - 1] : vals[0];
    }
    return null;
  };
  const out = { top_phone: pick(['Телефон'], false) || '' };
  if (wanted.nested_card) {
    const vals = byLabel['Карта'] || [];
    out.nested_card = vals.length >= 2 ? vals[vals.length - 1] : null;
  }
  if (wanted.phone) out.phone = pick(['Телефон'], true);
  if (wanted.account) out.account = pick(['Аккаунт'], true);
  if (wanted.merchant_id_sbp) out.merchant_id_sbp = pick(['MerchantId СБП'], true);
  if (wanted.account_number) {
    out.account_number = pick(
      ['Номер счёта', 'Номер счета', 'Номер расчёта', 'Номер расчета'],
      true
    );
  }
  return out;
}
"""


def verify_set_aggregate(
    page: "Page",
    intent: SetAggregateIntent,
    *,
    expected_top_phone: str,
) -> str | None:
    """Fast post-save checks — no switch, no full field wait.

    Confirms sole-active target via one aggregate snapshot, then reads only
    provided nested fields (+ top-level phone) in one DOM evaluate.
    """
    provided = intent.provided_optional()

    with _time_set_aggregate_step("verify_snapshot", card=intent.card):
        snap = snapshot_aggregate_checkboxes(page, card=intent.card)
        active = [e.name for e in snap if e.checked]
        if not (
            len(active) == 1
            and aggregate_label_matches(active[0], intent.aggregate)
        ):
            return (
                f"aggregate not sole active: expected={intent.aggregate!r} "
                f"active={active!r}"
            )
        if _snapshot_entry(snap, intent.aggregate) is None:
            return f"aggregate not found: {intent.aggregate!r}"

    with _time_set_aggregate_step("verify_fields", card=intent.card):
        aw = _aw()
        modal = page.locator(aw.MODAL_BODY)
        wanted = {
            "nested_card": True,
            "phone": "phone" in provided,
            "account": "account" in provided,
            "merchant_id_sbp": "merchant_id_sbp" in provided,
            "account_number": "account_number" in provided,
        }
        try:
            bundle = modal.evaluate(_VERIFY_FIELDS_JS, wanted)
        except Exception as exc:
            log.warning("⚠️ [SetAggregate] verify fields evaluate failed: %s", exc)
            bundle = {}

        if not isinstance(bundle, dict):
            bundle = {}

        nested_card = bundle.get("nested_card")
        if nested_card is not None and str(nested_card).strip():
            if not _values_match(intent.card, str(nested_card)):
                return (
                    f"nested card mismatch expected={mask_card(intent.card)} "
                    f"actual={mask_card(str(nested_card))}"
                )

        for key in ("phone", "account", "merchant_id_sbp", "account_number"):
            if key not in provided:
                continue
            actual = bundle.get(key)
            if actual is None or not _values_match(provided[key], str(actual)):
                return f"nested {key} mismatch"

        top_phone = str(bundle.get("top_phone") or "")
        if not _values_match(expected_top_phone, top_phone):
            if (expected_top_phone or "").strip() or top_phone.strip():
                return "top-level phone changed"

    return None


def apply_set_aggregate_on_open_form(
    page: "Page",
    intent: SetAggregateIntent,
    *,
    skip_switch_if_sole: bool = True,
) -> tuple[str | None, str]:
    """Switch aggregate + fill fields on an already-open form.

    Returns ``(error_result_code_or_None, top_level_phone_snapshot)``.
    """
    with _time_set_aggregate_step("set_aggregate", card=intent.card):
        top_phone = read_top_level_phone(page)
        log.info(
            "[SetAggregate] open_form card=%s aggregate=%s top_phone=%s active=%s",
            mask_card(intent.card),
            intent.aggregate,
            _mask_phone(top_phone),
            active_aggregate_names(page),
        )

        found = find_aggregate_state(page, intent.aggregate)
        if found is None:
            log.error(
                "❌ [SetAggregate] FAIL_AGGREGATE_NOT_FOUND aggregate=%s",
                intent.aggregate,
            )
            return RESULT_FAIL_AGGREGATE_NOT_FOUND, top_phone

        try:
            already_sole = skip_switch_if_sole and target_is_sole_active(
                page, intent.aggregate
            )
            if already_sole:
                log.info(
                    "[SetAggregate] target already sole active — skip checkbox toggles "
                    "aggregate=%s",
                    intent.aggregate,
                )
            else:
                switch_to_single_aggregate(
                    page, intent.aggregate, card=intent.card
                )
        except AggregateSwitchError as exc:
            log.error(
                "❌ [SetAggregate] FAIL_AGGREGATE_SWITCH card=%s aggregate=%s reason=%s",
                mask_card(intent.card),
                intent.aggregate,
                exc.reason,
            )
            return RESULT_FAIL_AGGREGATE_SWITCH, top_phone
        except Exception as exc:
            log.error(
                "❌ [SetAggregate] FAIL_AGGREGATE_SWITCH card=%s aggregate=%s reason=%s",
                mask_card(intent.card),
                intent.aggregate,
                exc,
            )
            return RESULT_FAIL_AGGREGATE_SWITCH, top_phone

        try:
            log.info(
                "[SetAggregate] waiting_requested_nested_fields aggregate=%s "
                "provided=%s",
                intent.aggregate,
                sorted(intent.provided_optional().keys()),
            )
            fields = wait_for_requested_nested_fields(
                page, intent, expected_top_phone=top_phone
            )
            fill_set_aggregate_nested_fields_from_locators(
                page, intent, fields, expected_top_phone=top_phone
            )
            log.info(
                "[SetAggregate] fill_completed card=%s aggregate=%s provided=%s",
                mask_card(intent.card),
                intent.aggregate,
                sorted(intent.provided_optional().keys()),
            )
        except Exception as exc:
            log.error(
                "❌ [SetAggregate] FAIL_AGGREGATE_FIELDS card=%s aggregate=%s reason=%s",
                mask_card(intent.card),
                intent.aggregate,
                exc,
            )
            return RESULT_FAIL_AGGREGATE_FIELDS, top_phone

        return None, top_phone


def map_save_outcome(outcome: "SaveWaitOutcome") -> str | None:
    """Map save wait outcome to FAIL_* or None when closed successfully."""
    if outcome.status == "closed":
        return None
    if outcome.status == "validation":
        return RESULT_FAIL_VALIDATION
    if outcome.status == "timeout":
        return RESULT_FAIL_SAVE_TIMEOUT
    return RESULT_FAIL_TECHNICAL


def verify_set_aggregate_after_save(
    page: "Page",
    intent: SetAggregateIntent,
    *,
    expected_top_phone: str,
) -> str:
    """Re-open wallet after shared Save and confirm aggregate state.

    Uses the shared delete/open flow: wait for form close → search ready →
    one card fill + Enter → strict match → row click (short first-click timeout,
    one fresh-locator retry, no second fill) → lightweight field checks.
    Does not click Save again and does not re-run switch logic.

    Returns ``OK_SET_AGGREGATE`` or a FAIL_* code.
    """
    from automation.engine import (
        CardSearchUnsettledError,
        OpenCardStageError,
        _close_stale_modal,
        _wait_form_hidden_after_delete,
        ensure_wallet_search_ready,
        find_strict_matching_row_index,
        open_matched_card_row,
    )

    with _time_set_aggregate_step("set_aggregate_verify", card=intent.card):
        # Save already waited for close; confirm UI is fully back on the list page.
        if not _wait_form_hidden_after_delete(page):
            log.warning(
                "⚠️ [SetAggregate] form still visible before verify — forcing close card=%s",
                mask_card(intent.card),
            )
            try:
                _close_stale_modal(page)
            except Exception:
                pass

        try:
            with _time_set_aggregate_step("verify_search", card=intent.card):
                if not ensure_wallet_search_ready(page, allow_goto=True):
                    log.error(
                        "❌ [SetAggregate] verify search UI not ready card=%s",
                        mask_card(intent.card),
                    )
                    return RESULT_FAIL_VERIFY
                verify_index = find_strict_matching_row_index(page, intent.card)
                if verify_index is None:
                    log.error(
                        "❌ [SetAggregate] verify card not found after save card=%s",
                        mask_card(intent.card),
                    )
                    return RESULT_FAIL_VERIFY

            with _time_set_aggregate_step("verify_open", card=intent.card):
                open_matched_card_row(page, intent.card, verify_index)
        except (CardSearchUnsettledError, OpenCardStageError) as exc:
            log.error("❌ [SetAggregate] verify open failed: %s", exc)
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return RESULT_FAIL_VERIFY

        reason = verify_set_aggregate(
            page, intent, expected_top_phone=expected_top_phone
        )
        try:
            _close_stale_modal(page)
        except Exception:
            pass

        if reason:
            log.error(
                "❌ [SetAggregate] FAIL_VERIFY card=%s reason=%s",
                mask_card(intent.card),
                reason,
            )
            return RESULT_FAIL_VERIFY

        log.info(
            "✅ [SetAggregate] OK_SET_AGGREGATE card=%s", mask_card(intent.card)
        )
        return RESULT_OK_SET_AGGREGATE


def save_shared_wallet_form(page: "Page", cfg: "RunConfig") -> str | None:
    """Click Save once for the open wallet form.

    Returns None on success, or FAIL_VALIDATION / FAIL_SAVE_TIMEOUT / FAIL_TECHNICAL.
    """
    if cfg.dry_run:
        return None
    aw = _aw()
    try:
        outcome = aw.save_add_wallet_modal(page)
    except Exception as exc:
        log.error("❌ [SetAggregate] shared save click failed: %s", exc)
        return RESULT_FAIL_SAVE_TIMEOUT
    return map_save_outcome(outcome)


def ensure_set_aggregate(
    page: "Page", intent: SetAggregateIntent, cfg: "RunConfig"
) -> str:
    """Standalone set_aggregate (single-action Excel): open → apply → save → verify.

    Grouped multi-action cards should use ``apply_set_aggregate_on_open_form`` +
    shared Save + ``verify_set_aggregate_after_save`` instead.
    """
    from automation.engine import (
        CardSearchUnsettledError,
        OpenCardStageError,
        _close_stale_modal,
        _pause_stop_before_save,
        find_and_open_card_for_delete,
        find_strict_matching_row_index,
    )

    log.info(
        "🧩 [SetAggregate] start card=%s aggregate=%s dry_run=%s stop_before_save=%s",
        mask_card(intent.card),
        intent.aggregate,
        cfg.dry_run,
        cfg.stop_before_save,
    )

    if not intent.aggregate:
        return RESULT_FAIL_TECHNICAL

    try:
        if cfg.dry_run:
            try:
                match_index = find_strict_matching_row_index(page, intent.card)
            except CardSearchUnsettledError:
                return RESULT_FAIL_TECHNICAL
            if match_index is None:
                return RESULT_SKIP_NOT_FOUND
            return RESULT_DRY_RUN_WOULD_SET_AGGREGATE

        try:
            match_index = find_and_open_card_for_delete(page, intent.card)
        except CardSearchUnsettledError:
            return RESULT_FAIL_TECHNICAL
        except OpenCardStageError:
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return RESULT_FAIL_OPEN_CARD

        if match_index is None:
            return RESULT_SKIP_NOT_FOUND

        err, top_phone = apply_set_aggregate_on_open_form(page, intent)
        if err:
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return err

        if cfg.stop_before_save:
            log.info(
                "ℹ️ [SetAggregate] stop_before_save — form ready, Save not clicked card=%s",
                mask_card(intent.card),
            )
            _pause_stop_before_save(page, cfg)
            return RESULT_STOP_BEFORE_SAVE

        save_err = save_shared_wallet_form(page, cfg)
        if save_err:
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return save_err

        return verify_set_aggregate_after_save(
            page, intent, expected_top_phone=top_phone
        )

    except Exception as exc:
        log.exception(
            "❌ [SetAggregate] technical failure card=%s: %s",
            mask_card(intent.card),
            exc,
        )
        try:
            from automation.engine import _close_stale_modal

            _close_stale_modal(page)
        except Exception:
            pass
        return RESULT_FAIL_TECHNICAL
