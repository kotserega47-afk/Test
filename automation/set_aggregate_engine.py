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


def fill_set_aggregate_nested_fields(page: "Page", intent: SetAggregateIntent) -> None:
    """Fill nested aggregate fields that exist; never require missing «Карта»."""
    with _time_set_aggregate_step("aggregate_fill", card=intent.card):
        aw = _aw()
        modal = page.locator(aw.MODAL_BODY)

        if _nested_field_visible(page, ("Карта",)):
            log.info(
                "[SetAggregate] fill_start nested_card=%s",
                mask_card(intent.card),
            )
            aw.fill_aggregate_field_if_present(
                modal, ("Карта",), intent.card, required=False
            )
            log.info(
                "[SetAggregate] fill_done field=card value=%s",
                mask_card(intent.card),
            )
        else:
            log.info(
                "[SetAggregate] fill_skip field=card reason=not_present_for_aggregate "
                "aggregate=%s search_card=%s",
                intent.aggregate,
                mask_card(intent.card),
            )

        provided = intent.provided_optional()
        if "phone" in provided:
            if not _nested_field_visible(page, ("Телефон",)):
                log.info(
                    "[SetAggregate] fill_skip field=phone reason=nested_phone_not_visible"
                )
            else:
                log.info(
                    "[SetAggregate] fill_start nested_phone=%s (top-level phone untouched)",
                    _mask_phone(provided["phone"]),
                )
                # Nested aggregate phone only (last «Телефон» when duplicates exist).
                aw.fill_aggregate_field_if_present(
                    modal, ("Телефон",), provided["phone"]
                )
                log.info(
                    "[SetAggregate] fill_done field=phone value=%s",
                    _mask_phone(provided["phone"]),
                )
        else:
            log.info(
                "[SetAggregate] fill_skip field=phone reason=column_absent_or_empty"
            )

        if "account" in provided:
            log.info(
                "[SetAggregate] fill_start field=account value=%s",
                _mask_secret_field(provided["account"]),
            )
            aw.fill_aggregate_field_if_present(modal, ("Аккаунт",), provided["account"])
            log.info("[SetAggregate] fill_done field=account")
        if "merchant_id_sbp" in provided:
            log.info(
                "[SetAggregate] fill_start field=merchant_id_sbp value=%s",
                _mask_secret_field(provided["merchant_id_sbp"]),
            )
            aw.fill_aggregate_field_if_present(
                modal, ("MerchantId СБП",), provided["merchant_id_sbp"]
            )
            log.info("[SetAggregate] fill_done field=merchant_id_sbp")
        if "account_number" in provided:
            log.info(
                "[SetAggregate] fill_start field=account_number value=%s",
                _mask_secret_field(provided["account_number"]),
            )
            aw.fill_aggregate_field_if_present(
                modal, aw.ACCOUNT_NUMBER_LABELS, provided["account_number"]
            )
            log.info("[SetAggregate] fill_done field=account_number")


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
                "[SetAggregate] waiting_nested_fields aggregate=%s",
                intent.aggregate,
            )
            wait_for_set_aggregate_fields_visible(
                page, intent.aggregate, card=intent.card
            )
            log.info(
                "[SetAggregate] nested_fields_visible aggregate=%s",
                intent.aggregate,
            )
        except Exception as exc:
            log.error(
                "❌ [SetAggregate] FAIL_AGGREGATE_FIELDS card=%s aggregate=%s reason=%s",
                mask_card(intent.card),
                intent.aggregate,
                exc,
            )
            return RESULT_FAIL_AGGREGATE_FIELDS, top_phone

        try:
            fill_set_aggregate_nested_fields(page, intent)
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
