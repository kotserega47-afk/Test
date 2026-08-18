"""Wallet Editor action ``set_aggregate`` — switch aggregate checkbox and nested fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from automation.add_wallet_engine import SaveWaitOutcome
    from automation.runtime import RunConfig

from automation.audit import log, mask_card
from automation.wallet_form_helpers import (
    MODAL_BODY,
    AggregateCheckboxSnapshot,  # noqa: F401
    AggregateSwitchError,
    NESTED_FIELD_LABELS,
    _checkbox_locator_by_id,  # noqa: F401
    _fresh_aggregate_checkbox,  # noqa: F401
    _inputs_for_labels,
    _list_labeled_inputs,
    _locator_for_labeled_input,
    _pick_nested_generic_item,  # noqa: F401
    _pick_nested_phone_item,  # noqa: F401
    _snapshot_entry,
    _wait_aggregate_checked_state,  # noqa: F401
    active_aggregate_names,
    aggregate_checkbox_for_label,  # noqa: F401
    aggregate_label_matches,
    css_escape_ident,  # noqa: F401
    fill_locator_confirmed as _fill_locator_confirmed,
    fill_locator_text,
    find_aggregate_checkbox_label,  # noqa: F401
    find_aggregate_state,
    list_aggregate_checkbox_states,  # noqa: F401
    mask_phone as _mask_phone,
    mask_secret_field as _mask_secret_field,
    normalize_label_text as _normalize_aggregate_label_text,  # noqa: F401
    opportunistic_nested_card_item,
    parse_aggregate_snapshot_raw,  # noqa: F401
    read_locator_value as _read_locator_value,
    read_top_level_phone,
    resolve_nested_field_locator,
    snapshot_aggregate_checkboxes as _snapshot_aggregate_checkboxes,
    switch_to_single_aggregate as _switch_to_single_aggregate,
    target_is_sole_active,
    time_form_step,
    values_match as _values_match,
    wait_for_requested_nested_fields as _wait_for_requested_nested_fields,
)
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
_AGGREGATE_FIELDS_WAIT_MS = 5_000
_NESTED_FIELD_POLL_MS = 50
_NESTED_FIELD_WAIT_MS = 5_000
_SET_AGGREGATE_TIMING_PROFILE = "wallet_editor"
_SET_AGGREGATE_TIMING_SCOPE = "set_aggregate"
_FIELD_LABELS = NESTED_FIELD_LABELS

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


def _time_set_aggregate_step(step: str, *, card: str | None = None):
    return time_form_step(
        step,
        card=card,
        profile=_SET_AGGREGATE_TIMING_PROFILE,
        scope=_SET_AGGREGATE_TIMING_SCOPE,
    )


def _css_escape_ident(value: str) -> str:
    return css_escape_ident(value)


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
    return _read_locator_value(field)


def snapshot_aggregate_checkboxes(page: "Page", *, card: str | None = None):
    return _snapshot_aggregate_checkboxes(
        page, card=card, timing_scope=_SET_AGGREGATE_TIMING_SCOPE
    )


def wait_for_requested_nested_fields(
    page: "Page",
    intent: SetAggregateIntent,
    *,
    expected_top_phone: str,
) -> dict[str, object]:
    """Wait only for Excel-provided nested fields; return ready locators."""
    return _wait_for_requested_nested_fields(
        page,
        needed=intent.provided_optional(),
        expected_top_phone=expected_top_phone,
        aggregate=intent.aggregate,
        card=intent.card,
        timing_scope=_SET_AGGREGATE_TIMING_SCOPE,
    )


def switch_to_single_aggregate(
    page: "Page", aggregate_name: str, *, card: str | None = None
) -> None:
    _switch_to_single_aggregate(
        page,
        aggregate_name,
        card=card,
        timing_scope=_SET_AGGREGATE_TIMING_SCOPE,
    )


def detect_set_aggregate_expansion(page: "Page") -> str | None:
    """Detect nested aggregate block without false top-level hits."""
    labeled = _list_labeled_inputs(page)
    for label in _SET_AGGREGATE_EXPANSION_LABELS:
        if label in {"Карта", "Телефон"}:
            continue
        items = _inputs_for_labels(labeled, (label,))
        for item in items:
            if item.get("visible"):
                return label
    for label in ("Карта", "Телефон"):
        items = _inputs_for_labels(labeled, (label,))
        visible = [i for i in items if i.get("visible")]
        if len(visible) >= 2:
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
            remaining_ms = (deadline - time.monotonic()) * 1000
            if remaining_ms <= 0:
                break
            page.wait_for_timeout(min(75, remaining_ms))
        raise RuntimeError(f"aggregate fields not visible: {aggregate_name}")


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
        return resolve_nested_field_locator(
            page, "phone", expected_top_phone=expected_top_phone
        )

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
            timing_scope=_SET_AGGREGATE_TIMING_SCOPE,
        )
        log.info(
            "[SetAggregate] fill_done field=phone value=%s",
            _mask_phone(provided["phone"]),
        )

    for key, labels in (
        ("account", ("Аккаунт",)),
        ("merchant_id_sbp", ("MerchantId СБП",)),
        ("account_number", NESTED_FIELD_LABELS["account_number"]),
    ):
        if key not in provided:
            continue
        field = fields.get(key)
        if field is None:
            raise RuntimeError(f"nested {key} locator missing after wait")

        def resolve_key(k=key):
            return resolve_nested_field_locator(page, k)

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
            timing_scope=_SET_AGGREGATE_TIMING_SCOPE,
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
        item = opportunistic_nested_card_item(page)
        if item is not None:
            loc = _locator_for_labeled_input(page, item)
            if loc is not None:
                log.info(
                    "[SetAggregate] fill_start nested_card=%s",
                    mask_card(intent.card),
                )
                try:
                    fill_locator_text(loc, "Карта", intent.card)
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
        modal = page.locator(MODAL_BODY)
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
