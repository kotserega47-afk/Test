"""Shared Wallet Editor form helpers (aggregate checkboxes, nested fields, fill).

Used by ``add_wallet_engine`` and ``set_aggregate_engine``. Must not import those
modules — keeps the graph acyclic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, TYPE_CHECKING

import re
import time

from automation.audit import log, log_timing, normalize_card_digits

if TYPE_CHECKING:
    from playwright.sync_api import Page

MODAL_BODY = "#wallet-add-modal___BV_modal_body_"

_AGGREGATE_TOGGLE_TIMEOUT_MS = 8_000
_AGGREGATE_TOGGLE_POLL_MS = 75
_AGGREGATE_UNCHECK_ROUNDS = 12
_NESTED_FIELD_POLL_MS = 50
_NESTED_FIELD_WAIT_MS = 5_000
# Cap for optional nested phone: after mandatory fields appear, or when only
# optional phone is requested (needed_keys empty).
_OPTIONAL_NESTED_FIELD_GRACE_MS = 1_000

_DEFAULT_TIMING_PROFILE = "wallet_editor"
_DEFAULT_TIMING_SCOPE = "set_aggregate"

_KYC_LABELS = ("KYC", "КУС", "Кус")
_KYC_EXACT_LABELS = frozenset(
    re.sub(r"\s+", " ", (label or "").strip()).casefold() for label in _KYC_LABELS
)

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

NESTED_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "phone": ("Телефон",),
    "account": ("Аккаунт",),
    "merchant_id_sbp": ("MerchantId СБП",),
    "account_number": (
        "Номер счёта",
        "Номер счета",
        "Номер расчёта",
        "Номер расчета",
    ),
    "card": ("Карта",),
}


class FieldNotEditableError(Exception):
    """Raised when a target input/textarea is readonly or disabled."""

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(f"field is readonly/disabled: {label}")


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


def time_form_step(
    step: str,
    *,
    card: str | None = None,
    profile: str = _DEFAULT_TIMING_PROFILE,
    scope: str = _DEFAULT_TIMING_SCOPE,
):
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
                profile=profile,
                scope=scope,
                step=step,
                duration_ms=duration_ms,
                outcome=self.outcome,
                card=card,
            )
            return False

    return _Timer()


def mask_phone(value: object) -> str:
    digits = "".join(c for c in str(value or "") if c.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def mask_secret_field(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) >= 6:
        return mask_phone(digits)
    if len(text) <= 4:
        return "***"
    return f"***{text[-4:]}"


def normalize_label_text(text: str) -> str:
    """Collapse whitespace from label.inner_text (may include nested <span>)."""
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def labels_match(actual: str, expected: str) -> bool:
    return normalize_label_text(actual).casefold() == normalize_label_text(expected).casefold()


def aggregate_label_matches(text: str, aggregate_name: str) -> bool:
    left = normalize_label_text(text).casefold()
    right = normalize_label_text(aggregate_name).casefold()
    return bool(left) and left == right


def is_kyc_label_text(text: str) -> bool:
    return normalize_label_text(text).casefold() in _KYC_EXACT_LABELS


def is_sim_a_aggregate(aggregate_name: str) -> bool:
    return normalize_label_text(aggregate_name).casefold().startswith("sim a")


def css_escape_ident(value: str) -> str:
    """Minimal CSS ident escape for getElementById-style selectors."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _attribute_is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().casefold()
    return normalized not in {"", "false", "0", "none"}


def is_field_editable(field) -> bool:
    try:
        return bool(field.evaluate("el => !el.readOnly && !el.disabled"))
    except Exception:
        pass
    try:
        readonly = _attribute_is_truthy(field.get_attribute("readonly"))
        disabled = _attribute_is_truthy(field.get_attribute("disabled"))
        return not (readonly or disabled)
    except Exception:
        return True


def assert_field_editable(field, label: str, *, log_prefix: str = "[WalletForm]") -> None:
    if is_field_editable(field):
        return
    log.info(f"{log_prefix} field_not_editable label={label}")
    raise FieldNotEditableError(label)


def fill_locator_text(field, label: str, value: str, *, log_prefix: str = "[WalletForm]") -> None:
    assert_field_editable(field, label, log_prefix=log_prefix)
    field.fill("")
    field.fill(value)


def clear_locator_text(field, label: str, *, log_prefix: str = "[WalletForm]") -> None:
    assert_field_editable(field, label, log_prefix=log_prefix)
    field.fill("")


def values_match(expected: str, actual: str | None) -> bool:
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


def read_locator_value(field) -> str:
    try:
        return str(field.input_value() or "").strip()
    except Exception:
        try:
            return str(field.evaluate("el => el.value") or "").strip()
        except Exception:
            return ""


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
            escaped = css_escape_ident(for_id)
            checkbox = modal.locator(f'input[type="checkbox"][id="{escaped}"]')
            try:
                if checkbox.count() > 0:
                    return checkbox.first
            except Exception:
                pass
            return None

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
            text = normalize_label_text(label_el.inner_text(timeout=500))
        except Exception:
            continue
        if not aggregate_label_matches(text, aggregate_name):
            continue
        if aggregate_checkbox_for_label(modal, label_el) is not None:
            return label_el
    return None


def parse_aggregate_snapshot_raw(raw: object) -> list[AggregateCheckboxSnapshot]:
    """Normalize evaluate() payload into AggregateCheckboxSnapshot list."""
    out: list[AggregateCheckboxSnapshot] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = normalize_label_text(str(item.get("name") or ""))
        checkbox_id = str(item.get("id") or "").strip()
        if not name or not checkbox_id:
            continue
        if is_kyc_label_text(name):
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
    page: "Page",
    *,
    card: str | None = None,
    timing_scope: str = _DEFAULT_TIMING_SCOPE,
) -> list[AggregateCheckboxSnapshot]:
    """One DOM evaluate → real aggregate checkboxes only (no field captions)."""
    modal = page.locator(MODAL_BODY)
    with time_form_step("aggregate_snapshot", card=card, scope=timing_scope):
        try:
            raw = modal.evaluate(_AGGREGATE_SNAPSHOT_JS)
        except Exception as exc:
            log.warning("⚠️ [WalletForm] aggregate snapshot failed: %s", exc)
            return []
        return parse_aggregate_snapshot_raw(raw)


def list_aggregate_checkbox_states(page: "Page") -> list[tuple[str, bool]]:
    return [(e.name, e.checked) for e in snapshot_aggregate_checkboxes(page)]


def find_aggregate_state(page: "Page", aggregate_name: str) -> bool | None:
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
    modal = page.locator(MODAL_BODY)
    escaped = css_escape_ident(checkbox_id)
    checkbox = modal.locator(f'input[type="checkbox"][id="{escaped}"]')
    try:
        if checkbox.count() == 0:
            return None
    except Exception:
        return None
    return checkbox.first


def _fresh_aggregate_checkbox(page: "Page", aggregate_name: str):
    entry = _snapshot_entry(snapshot_aggregate_checkboxes(page), aggregate_name)
    if entry is None:
        return None
    return _checkbox_locator_by_id(page, entry.id)


def _wait_aggregate_checked_state(
    page: "Page",
    aggregate_name: str,
    *,
    want_checked: bool,
    timeout_ms: int = _AGGREGATE_TOGGLE_TIMEOUT_MS,
    timing_scope: str = _DEFAULT_TIMING_SCOPE,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_state: bool | None = None
    while time.monotonic() < deadline:
        snap = snapshot_aggregate_checkboxes(page, timing_scope=timing_scope)
        entry = _snapshot_entry(snap, aggregate_name)
        if entry is not None:
            last_state = entry.checked
            if entry.checked is want_checked:
                return True
        remaining_ms = (deadline - time.monotonic()) * 1000
        if remaining_ms <= 0:
            break
        page.wait_for_timeout(min(_AGGREGATE_TOGGLE_POLL_MS, remaining_ms))
    log.info(
        "[WalletForm] wait_checked_timeout aggregate=%s want_checked=%s last_state=%s",
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
    timing_scope: str = _DEFAULT_TIMING_SCOPE,
) -> bool:
    with time_form_step("aggregate_switch_sole_active", card=card, scope=timing_scope):
        deadline = time.monotonic() + timeout_ms / 1000.0
        last_active: list[str] = []
        while time.monotonic() < deadline:
            snap = snapshot_aggregate_checkboxes(page, card=card, timing_scope=timing_scope)
            last_active = [e.name for e in snap if e.checked]
            if len(last_active) == 1 and aggregate_label_matches(
                last_active[0], aggregate_name
            ):
                log.info(
                    "[WalletForm] sole_active_confirmed aggregate=%s active=%s",
                    aggregate_name,
                    last_active,
                )
                return True
            remaining_ms = (deadline - time.monotonic()) * 1000
            if remaining_ms <= 0:
                break
            page.wait_for_timeout(min(_AGGREGATE_TOGGLE_POLL_MS, remaining_ms))
        log.info(
            "[WalletForm] sole_active_timeout aggregate=%s active=%s",
            aggregate_name,
            last_active,
        )
        return False


def switch_to_single_aggregate(
    page: "Page",
    aggregate_name: str,
    *,
    card: str | None = None,
    timing_scope: str = _DEFAULT_TIMING_SCOPE,
) -> None:
    """Uncheck other aggregates and check the target using fast DOM snapshots."""
    with time_form_step("aggregate_switch", card=card, scope=timing_scope):
        snap = snapshot_aggregate_checkboxes(page, card=card, timing_scope=timing_scope)
        before_active = [e.name for e in snap if e.checked]
        log.info(
            "[WalletForm] switch_start target=%s active_before=%s",
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
                "[WalletForm] aggregate already sole active aggregate=%s — skip toggles",
                aggregate_name,
            )
            return

        for round_idx in range(_AGGREGATE_UNCHECK_ROUNDS):
            snap = snapshot_aggregate_checkboxes(page, card=card, timing_scope=timing_scope)
            others = [
                e
                for e in snap
                if e.checked and not aggregate_label_matches(e.name, aggregate_name)
            ]
            if not others:
                log.info(
                    "[WalletForm] no_other_active_left target=%s round=%s",
                    aggregate_name,
                    round_idx,
                )
                break

            other = others[0]
            log.info(
                "[WalletForm] uncheck_start aggregate=%s remaining_others=%s",
                other.name,
                [e.name for e in others],
            )
            with time_form_step("aggregate_switch_uncheck", card=card, scope=timing_scope):
                checkbox = _checkbox_locator_by_id(page, other.id)
                if checkbox is None:
                    snap = snapshot_aggregate_checkboxes(
                        page, card=card, timing_scope=timing_scope
                    )
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
                        "[WalletForm] uncheck_click_error aggregate=%s err=%s — will re-query",
                        other.name,
                        type(exc).__name__,
                    )

                if not _wait_aggregate_checked_state(
                    page,
                    other.name,
                    want_checked=False,
                    timing_scope=timing_scope,
                ):
                    raise AggregateSwitchError(
                        f"timeout waiting unchecked aggregate={other.name} "
                        f"active={[e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]}"
                    )
            log.info(
                "[WalletForm] uncheck_confirmed aggregate=%s active_now=%s",
                other.name,
                [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked],
            )
        else:
            raise AggregateSwitchError(
                f"failed to uncheck other aggregates target={aggregate_name} "
                f"active={[e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]}"
            )

        log.info("[WalletForm] check_start aggregate=%s", aggregate_name)
        with time_form_step("aggregate_switch_check", card=card, scope=timing_scope):
            snap = snapshot_aggregate_checkboxes(page, card=card, timing_scope=timing_scope)
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
                snap = snapshot_aggregate_checkboxes(
                    page, card=card, timing_scope=timing_scope
                )
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
                        "[WalletForm] check_click_error aggregate=%s err=%s — will re-query",
                        aggregate_name,
                        type(exc).__name__,
                    )
                    snap = snapshot_aggregate_checkboxes(
                        page, card=card, timing_scope=timing_scope
                    )
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
                log.info("[WalletForm] check_clicked aggregate=%s", aggregate_name)
            else:
                log.info("[WalletForm] check_already_on aggregate=%s", aggregate_name)

            if not _wait_aggregate_checked_state(
                page,
                aggregate_name,
                want_checked=True,
                timing_scope=timing_scope,
            ):
                raise AggregateSwitchError(
                    f"timeout waiting checked aggregate={aggregate_name} "
                    f"active={[e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]}"
                )
        log.info(
            "[WalletForm] check_confirmed aggregate=%s active_now=%s",
            aggregate_name,
            [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked],
        )

        if not _wait_target_sole_active(
            page, aggregate_name, card=card, timing_scope=timing_scope
        ):
            active = [e.name for e in snapshot_aggregate_checkboxes(page) if e.checked]
            raise AggregateSwitchError(
                f"timeout waiting sole active expected={aggregate_name!r} active={active!r}"
            )


def _list_labeled_inputs(page: "Page") -> list[dict]:
    modal = page.locator(MODAL_BODY)
    try:
        raw = modal.evaluate(_LIST_LABELED_INPUTS_JS)
    except Exception as exc:
        log.warning("⚠️ [WalletForm] labeled inputs evaluate failed: %s", exc)
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _inputs_for_labels(labeled: list[dict], labels: tuple[str, ...]) -> list[dict]:
    wanted = {normalize_label_text(x).casefold() for x in labels}
    out: list[dict] = []
    for item in labeled:
        name = normalize_label_text(str(item.get("label") or ""))
        if name.casefold() in wanted:
            out.append(item)
    return out


def _locator_for_labeled_input(page: "Page", item: dict):
    """Fresh Playwright locator for a labeled-input snapshot entry."""
    modal = page.locator(MODAL_BODY)
    input_id = str(item.get("id") or "").strip()
    if input_id:
        escaped = css_escape_ident(input_id)
        loc = modal.locator(f'input[id="{escaped}"]')
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass
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
    return None


def _pick_nested_generic_item(items: list[dict]) -> dict | None:
    visible = [
        p
        for p in items
        if p.get("visible") and not p.get("disabled") and not p.get("readOnly")
    ]
    if not visible:
        return None
    return visible[-1]


def _pick_nested_card_item(card_items: list[dict]) -> dict | None:
    visible = [c for c in card_items if c.get("visible")]
    if len(visible) < 2:
        return None
    return visible[-1]


def read_top_level_phone(page: "Page") -> str:
    """Main wallet phone (first «Телефон» input) — never the nested aggregate phone."""
    labeled = _list_labeled_inputs(page)
    phones = _inputs_for_labels(labeled, NESTED_FIELD_LABELS["phone"])
    visible = [p for p in phones if p.get("visible")]
    if not visible:
        return ""
    return str(visible[0].get("value") or "").strip()


def _nonempty_field_keys(spec: Mapping[str, str] | set[str] | None) -> set[str]:
    if not spec:
        return set()
    if isinstance(spec, Mapping):
        return {key for key, value in spec.items() if str(value or "").strip()}
    return {str(key) for key in spec if str(key or "").strip()}


def wait_for_requested_nested_fields(
    page: "Page",
    *,
    needed: Mapping[str, str] | set[str],
    expected_top_phone: str,
    aggregate: str = "",
    card: str | None = None,
    timing_scope: str = _DEFAULT_TIMING_SCOPE,
    optional_keys: Mapping[str, str] | set[str] | None = None,
) -> dict[str, object]:
    """Wait only for requested nested fields; return ready locators.

    One ``modal.evaluate`` per poll. Does not wait for Device. Nested «Карта»
    is waited only when ``card`` is in ``needed``. Phone wait excludes the
    top-level wallet phone. Wall-clock timeout is ``_NESTED_FIELD_WAIT_MS``.

    ``optional_keys`` (typically nested phone for Add Wallet) are collected
    opportunistically: if they appear, they are returned; if not, they are
    omitted without failing the wait.
    """
    needed_keys = _nonempty_field_keys(needed)
    optional_keys_set = _nonempty_field_keys(optional_keys) - needed_keys
    want_phone = "phone" in needed_keys or "phone" in optional_keys_set

    with time_form_step("requested_fields_wait", card=card, scope=timing_scope):
        if not needed_keys and not optional_keys_set:
            log.info(
                "[WalletForm] nested_fields_skip reason=no_optional_fields aggregate=%s",
                aggregate,
            )
            return {}

        found: dict[str, object] = {}
        deadline = time.monotonic() + _NESTED_FIELD_WAIT_MS / 1000.0
        phone_search_count = 0
        phone_wait_started: float | None = None
        required_done_at: float | None = None

        def _optional_deadline(now: float) -> float:
            if "phone" not in optional_keys_set or "phone" in found:
                return now
            if required_done_at is None:
                return deadline
            return min(
                deadline,
                required_done_at + _OPTIONAL_NESTED_FIELD_GRACE_MS / 1000.0,
            )

        while True:
            now = time.monotonic()
            if now >= deadline:
                break

            labeled = _list_labeled_inputs(page)

            if want_phone and "phone" not in found:
                if phone_wait_started is None:
                    phone_wait_started = time.perf_counter()
                phone_search_count += 1
                phones = _inputs_for_labels(labeled, NESTED_FIELD_LABELS["phone"])
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
                            profile=_DEFAULT_TIMING_PROFILE,
                            scope=timing_scope,
                            step="nested_phone_wait",
                            duration_ms=duration_ms,
                            outcome="ok",
                            card=card,
                        )
                        log.info(
                            "[WalletForm] nested_phone_found searches=%s "
                            "wait_ms=%s aggregate=%s top_phone=%s",
                            phone_search_count,
                            duration_ms,
                            aggregate,
                            mask_phone(expected_top_phone),
                        )

            for key in ("account", "merchant_id_sbp", "account_number", "card"):
                if key not in needed_keys or key in found:
                    continue
                items = _inputs_for_labels(labeled, NESTED_FIELD_LABELS[key])
                if key == "card":
                    item = _pick_nested_card_item(items)
                else:
                    item = _pick_nested_generic_item(items)
                    if item is None and len(items) == 1 and items[0].get("visible"):
                        item = items[0]
                if item is not None:
                    loc = _locator_for_labeled_input(page, item)
                    if loc is not None:
                        found[key] = loc

            missing_required = needed_keys - set(found.keys())
            missing_optional = optional_keys_set - set(found.keys())
            if not missing_required:
                if required_done_at is None:
                    required_done_at = time.monotonic()
                if not missing_optional or time.monotonic() >= _optional_deadline(
                    time.monotonic()
                ):
                    break

            remaining_ms = (deadline - time.monotonic()) * 1000
            if not missing_required:
                remaining_ms = min(
                    remaining_ms,
                    (_optional_deadline(time.monotonic()) - time.monotonic()) * 1000,
                )
            if remaining_ms <= 0:
                break
            page.wait_for_timeout(min(_NESTED_FIELD_POLL_MS, remaining_ms))

        if phone_wait_started is not None and "phone" not in found:
            duration_ms = round((time.perf_counter() - phone_wait_started) * 1000)
            phone_optional = "phone" in optional_keys_set
            log_timing(
                profile=_DEFAULT_TIMING_PROFILE,
                scope=timing_scope,
                step="nested_phone_wait",
                duration_ms=duration_ms,
                outcome="skip" if phone_optional else "fail",
                card=card,
            )
            if phone_optional:
                log.info(
                    "[WalletForm] nested_phone_not_present searches=%s "
                    "wait_ms=%s aggregate=%s",
                    phone_search_count,
                    duration_ms,
                    aggregate,
                )

        missing = sorted(needed_keys - set(found.keys()))
        if missing:
            raise RuntimeError(
                f"requested nested fields not visible: {missing} aggregate={aggregate}"
            )
        return found


def fill_locator_confirmed(
    page: "Page",
    *,
    field,
    value: str,
    label: str,
    resolve_fresh: Callable[[], object | None],
    card: str | None = None,
    timing_prefix: str = "nested_phone",
    timing_scope: str = _DEFAULT_TIMING_SCOPE,
) -> None:
    """Fill a known locator; confirm value; one fresh-locator retry if needed."""
    current = field
    attempts = 0
    with time_form_step(f"{timing_prefix}_fill", card=card, scope=timing_scope):
        for attempt in range(1, 3):
            attempts = attempt
            try:
                fill_locator_text(current, label, value)
            except Exception as exc:
                if attempt >= 2:
                    raise
                log.info(
                    "[WalletForm] %s_fill_error attempt=%s err=%s — retry fresh",
                    timing_prefix,
                    attempt,
                    type(exc).__name__,
                )
                current = resolve_fresh()
                if current is None:
                    raise
                continue

            with time_form_step(
                f"{timing_prefix}_confirm", card=card, scope=timing_scope
            ):
                actual = read_locator_value(current)
                if values_match(value, actual):
                    log.info(
                        "[WalletForm] %s_fill_confirmed attempts=%s value=%s",
                        timing_prefix,
                        attempts,
                        mask_secret_field(value)
                        if timing_prefix != "nested_phone"
                        else mask_phone(value),
                    )
                    return

            log.info(
                "[WalletForm] %s_fill_mismatch attempt=%s — retry fresh locator",
                timing_prefix,
                attempt,
            )
            current = resolve_fresh()
            if current is None:
                break

    raise RuntimeError(f"failed to set nested field {label}")


def resolve_nested_field_locator(
    page: "Page",
    key: str,
    *,
    expected_top_phone: str = "",
):
    """One evaluate + pick nested locator for ``key`` (phone/account/card/...)."""
    labeled = _list_labeled_inputs(page)
    labels = NESTED_FIELD_LABELS[key]
    items = _inputs_for_labels(labeled, labels)
    if key == "phone":
        item = _pick_nested_phone_item(items, expected_top_phone=expected_top_phone)
    elif key == "card":
        item = _pick_nested_card_item(items)
    else:
        item = _pick_nested_generic_item(items)
        if item is None and len(items) == 1 and items[0].get("visible"):
            item = items[0]
    return _locator_for_labeled_input(page, item) if item else None


def opportunistic_nested_card_item(page: "Page") -> dict | None:
    labeled = _list_labeled_inputs(page)
    cards = _inputs_for_labels(labeled, NESTED_FIELD_LABELS["card"])
    return _pick_nested_card_item(cards)


def requested_nested_fields_for_add_row(row) -> dict[str, str]:
    """Mandatory nested fields to wait/fill for Add Wallet — never Device.

    Nested phone is optional and is handled separately after aggregate switch.
    Nested Card is waited only when the aggregate is known to provide it.
    """
    out: dict[str, str] = {}
    account = str(getattr(row, "account", "") or "").strip()
    if account:
        out["account"] = account
    merchant = str(getattr(row, "merchant_id_sbp", "") or "").strip()
    if merchant:
        out["merchant_id_sbp"] = merchant
    account_number = str(getattr(row, "account_number", "") or "").strip()
    if account_number:
        out["account_number"] = account_number

    aggregate = str(getattr(row, "aggregate", "") or "").strip()
    card = str(getattr(row, "card", "") or "").strip()
    if card and aggregate and not is_sim_a_aggregate(aggregate):
        out["card"] = card
    return out
