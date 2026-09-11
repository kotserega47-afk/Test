"""Locate the wallet-edit «terminal / partner» multiselect.

The Antares control was historically found via
``input[placeholder='Партнеры']``. Operators confirmed the visible name is now
«Привязан к терминалу». Live DOM (label vs placeholder vs both, and the
card-data ready signal) was not inspected in this change. The helper matches
the confirmed name on label and placeholder, and keeps the old name as
compatibility.

Chips are never read while the root still shows in-flight data
(``multiselect--loading``, ``aria-busy``, or a visible spinner):
``_wait_field_loaded()`` continues until that state clears, then reads chips
or a proven empty. A visible placeholder is not a load timer and is not
proof of ready data.

``loaded_empty`` requires a confirmed settle for **this card and this form
opening**: loading observed then cleared with no chips, or a full chip read
(including a later local removal of the last chip). That readiness is kept
between calls on the same opening so a second read of an already-empty field
does not wait for a new loading transition. It is dropped on close, card
change, and when loading starts again, and is never reused for the next card.

Live DOM (LAURA / Antares wallet form) was not inspected in this change.
Whether production uses root ``multiselect--loading`` as the ready signal is
unconfirmed.

Action names, registry fields, and partner identifiers are unchanged.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page

from automation.audit import log

MODAL_BODY = "#wallet-add-modal___BV_modal_body_"

# Confirmed current UI name, then historical name. Not a claim that the new
# name is specifically a placeholder — both label and placeholder are tried.
TERMINAL_FIELD_NAMES = ("Привязан к терминалу", "Партнеры")

FAIL_TERMINAL_FIELD_NOT_FOUND = "FAIL_TERMINAL_FIELD_NOT_FOUND"
FAIL_TERMINAL_FIELD_AMBIGUOUS = "FAIL_TERMINAL_FIELD_AMBIGUOUS"
FAIL_TERMINAL_FIELD_NOT_LOADED = "FAIL_TERMINAL_FIELD_NOT_LOADED"
FAIL_PARTNER_OPTION_NOT_FOUND = "FAIL_PARTNER_OPTION_NOT_FOUND"
FAIL_ADD_NOT_CONFIRMED = "FAIL_ADD_NOT_CONFIRMED"
FAIL_SAVE_NOT_CONFIRMED = "FAIL_SAVE_NOT_CONFIRMED"
SKIP_BLOCKED_BY_ADD_FAILURE = "skip: blocked by add_partner failure"

_FIELD_WAIT_MS = 4000
_DROPDOWN_WAIT_MS = 5000
_OUTER_MULTISELECT_XPATH = (
    "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' multiselect ')][1]"
)
_ROW_MULTISELECT_CSS = "div.multiselect"


class TerminalFieldError(Exception):
    """Terminal/partner field cannot be read or is ambiguous."""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class TerminalField:
    """One resolved Vue-multiselect inside the current wallet form."""

    multiselect: Any
    source: str  # "label" | "placeholder"
    matched_name: str
    chip_pairs: list[tuple[Any, str]]
    loaded_empty: bool


def _normalize_label_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def labels_match(actual: str, expected: str) -> bool:
    return _normalize_label_text(actual) == _normalize_label_text(expected)


def partner_text_matches(actual: str, expected: str) -> bool:
    """Exact identity after whitespace/case fold — never substring."""
    if not (expected or "").strip() or not (actual or "").strip():
        return False
    return labels_match(actual, expected)


def _element_equal(left: Any, right: Any) -> bool:
    try:
        handle = right.element_handle(timeout=500)
        if handle is None:
            return False
        return bool(left.evaluate("(el, other) => el === other", handle))
    except Exception:
        return False


def _unique_locators(candidates: list[tuple[Any, str, str]]) -> list[tuple[Any, str, str]]:
    unique: list[tuple[Any, str, str]] = []
    for loc, source, name in candidates:
        if any(_element_equal(loc, other) for other, _src, _name in unique):
            continue
        unique.append((loc, source, name))
    return unique


def _collect_by_label(modal: Any, names: tuple[str, ...]) -> list[tuple[Any, str, str]]:
    found: list[tuple[Any, str, str]] = []
    rows = modal.locator("div.row")
    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            if not row.is_visible():
                continue
        except Exception:
            continue
        labels = row.locator("label")
        if labels.count() == 0:
            continue
        try:
            label = labels.first.inner_text(timeout=2000).strip()
        except Exception:
            continue
        matched = next((name for name in names if labels_match(label, name)), None)
        if matched is None:
            continue
        boxes = row.locator(_ROW_MULTISELECT_CSS)
        count = boxes.count()
        if count == 0:
            continue
        if count > 1:
            raise TerminalFieldError(
                FAIL_TERMINAL_FIELD_AMBIGUOUS,
                f"в строке «{matched}» несколько multiselect",
                retryable=False,
            )
        found.append((boxes.first, "label", matched))
    return found


def _collect_by_placeholder(modal: Any, names: tuple[str, ...]) -> list[tuple[Any, str, str]]:
    found: list[tuple[Any, str, str]] = []
    for name in names:
        inputs = modal.locator(f"input[placeholder='{name}']")
        count = inputs.count()
        for i in range(count):
            inp = inputs.nth(i)
            try:
                if not inp.is_visible():
                    continue
            except Exception:
                continue
            boxes = inp.locator(_OUTER_MULTISELECT_XPATH)
            if boxes.count() == 0:
                continue
            found.append((boxes.first, "placeholder", name))
    return found


def _observe(fn, message: str):
    try:
        return fn()
    except TerminalFieldError:
        raise
    except Exception as exc:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_LOADED,
            message,
            retryable=True,
        ) from exc


def _observe_bool(fn, message: str) -> bool:
    return bool(_observe(fn, message))


def _field_is_loading(multiselect: Any) -> bool:
    """True when the root control or its spinner still shows in-flight data.

    Vue-multiselect puts ``multiselect--loading`` on the root ``.multiselect``,
    not on a descendant. A descendant-only query would miss that state and
    treat a visible placeholder as already loaded.
    """
    class_name = str(
        _observe(
            lambda: (multiselect.get_attribute("class") or ""),
            "не удалось проверить состояние загрузки поля терминалов",
        )
        or ""
    )
    if " multiselect--loading " in f" {class_name} ":
        return True
    aria_busy = str(
        _observe(
            lambda: (multiselect.get_attribute("aria-busy") or ""),
            "не удалось проверить состояние загрузки поля терминалов",
        )
        or ""
    )
    if aria_busy.strip().casefold() == "true":
        return True
    spinner = multiselect.locator(".multiselect__spinner, .multiselect__loading")
    count = _observe_bool(
        lambda: spinner.count() > 0,
        "не удалось проверить состояние загрузки поля терминалов",
    )
    if not count:
        return False
    return _observe_bool(
        lambda: spinner.first.is_visible(),
        "не удалось проверить состояние загрузки поля терминалов",
    )


def _placeholder_visible(multiselect: Any) -> bool:
    placeholder = multiselect.locator(".multiselect__placeholder")
    count = _observe_bool(
        lambda: placeholder.count() > 0,
        "не удалось проверить признак пустого поля терминалов",
    )
    if not count:
        return False
    return _observe_bool(
        lambda: placeholder.first.is_visible(),
        "не удалось проверить признак пустого поля терминалов",
    )


@dataclass
class _TerminalFormSession:
    """Proven chip-field readiness for one page + form opening + card."""

    key: tuple[int, str, str]
    ready: bool = False


_form_session: _TerminalFormSession | None = None


def clear_terminal_form_session(page: Page | None = None) -> None:
    """Drop confirmed field readiness. Call when the wallet form closes."""
    global _form_session
    if page is not None:
        try:
            page.evaluate(
                """() => {
                  const el = document.querySelector('#wallet-add-modal___BV_modal_body_');
                  if (el) delete el.dataset.weFieldSession;
                }"""
            )
        except Exception:
            pass
    _form_session = None


def _modal_open_token(page: Page) -> str:
    """Stable id for the current modal node; a new node is a new opening."""
    try:
        token = page.evaluate(
            """() => {
              const el = document.querySelector('#wallet-add-modal___BV_modal_body_');
              if (!el) return '';
              if (!el.dataset.weFieldSession) {
                el.dataset.weFieldSession = `${Date.now()}-${Math.random()}`;
              }
              return el.dataset.weFieldSession;
            }"""
        )
    except Exception:
        return ""
    return token if isinstance(token, str) else ""


def _read_open_card_digits(page: Page) -> str:
    try:
        modal = page.locator(MODAL_BODY)
        rows = modal.locator("div.row")
        n = rows.count()
        if not isinstance(n, int):
            return ""
        for i in range(n):
            row = rows.nth(i)
            labels = row.locator("label")
            if labels.count() == 0:
                continue
            try:
                label = labels.first.inner_text(timeout=500).strip()
            except Exception:
                continue
            if not labels_match(label, "Карта"):
                continue
            inputs = row.locator("input[type='text']")
            if inputs.count() == 0:
                continue
            try:
                value = inputs.first.input_value()
            except Exception:
                continue
            digits = "".join(ch for ch in (value or "") if ch.isdigit())
            if digits:
                return digits
    except Exception:
        return ""
    return ""


def _session_key(page: Page) -> tuple[int, str, str]:
    return (id(page), _modal_open_token(page), _read_open_card_digits(page))


def _session_for(page: Page) -> _TerminalFormSession:
    global _form_session
    key = _session_key(page)
    if _form_session is None or _form_session.key != key:
        _form_session = _TerminalFormSession(key=key, ready=False)
    return _form_session


def _read_all_chips(multiselect: Any) -> tuple[int, list[tuple[Any, str]] | None]:
    """Return (dom_count, pairs). pairs is None when chips exist but are not ready.

    A readable subset is never returned: either every chip is read, or this
    raises a technical FAIL.
    """
    chips = multiselect.locator(".multiselect__tag")
    count = int(
        _observe(
            lambda: chips.count(),
            "не удалось прочитать выбранные терминалы",
        )
        or 0
    )
    if count == 0:
        return 0, []

    pairs: list[tuple[Any, str]] = []
    for i in range(count):
        chip = chips.nth(i)
        visible = _observe_bool(
            lambda chip=chip: chip.is_visible(),
            "не удалось прочитать выбранные терминалы",
        )
        if not visible:
            return count, None
        text = str(
            _observe(
                lambda chip=chip: (chip.inner_text() or "").strip(),
                "не удалось прочитать выбранный терминал",
            )
            or ""
        )
        if not text:
            raise TerminalFieldError(
                FAIL_TERMINAL_FIELD_NOT_LOADED,
                "не удалось прочитать выбранный терминал",
                retryable=True,
            )
        pairs.append((chip, text))
    return count, pairs


def _wait_field_loaded(multiselect: Any) -> list[tuple[Any, str]]:
    tags = multiselect.locator(".multiselect__tags")
    try:
        tags.first.wait_for(state="visible", timeout=_FIELD_WAIT_MS)
    except Exception as exc:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_LOADED,
            "поле терминалов найдено, но не загрузилось",
            retryable=True,
        ) from exc

    page = multiselect.page
    session = _session_for(page)
    field_deadline = time.monotonic() + (_FIELD_WAIT_MS / 1000.0)
    saw_root_loading = False
    while True:
        loading = _field_is_loading(multiselect)
        if loading:
            session.ready = False
            saw_root_loading = True
            if time.monotonic() >= field_deadline:
                raise TerminalFieldError(
                    FAIL_TERMINAL_FIELD_NOT_LOADED,
                    "поле терминалов не вышло из состояния загрузки",
                    retryable=True,
                )
            page.wait_for_timeout(100)
            continue

        session = _session_for(page)
        chip_count, pairs = _read_all_chips(multiselect)
        if chip_count > 0:
            if pairs is not None:
                session.ready = True
                return pairs
        elif saw_root_loading:
            # Loading was observed then cleared with no chips: empty is settled
            # for this opening. Placeholder is logged, not used as a load timer.
            session.ready = True
            log.info(
                "[Partners] stage=read empty_after_loading placeholder=%s",
                _placeholder_visible(multiselect),
            )
            return []
        elif session.ready:
            # Same card/form already proven; empty re-read or last chip removed.
            return []

        if time.monotonic() >= field_deadline:
            raise TerminalFieldError(
                FAIL_TERMINAL_FIELD_NOT_LOADED,
                "поле терминалов найдено, но готовность данных не подтверждена",
                retryable=True,
            )
        page.wait_for_timeout(100)


def resolve_terminal_field(page: Page) -> TerminalField:
    """Find the single terminal/partner multiselect in the open wallet form."""
    modal = page.locator(MODAL_BODY)
    try:
        modal.wait_for(state="visible", timeout=_FIELD_WAIT_MS)
    except Exception as exc:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_FOUND,
            "форма редактирования кошелька не видна",
            retryable=True,
        ) from exc

    candidates = _unique_locators(
        _collect_by_label(modal, TERMINAL_FIELD_NAMES)
        + _collect_by_placeholder(modal, TERMINAL_FIELD_NAMES)
    )
    if not candidates:
        log.error("[Partners] stage=find code=%s", FAIL_TERMINAL_FIELD_NOT_FOUND)
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_FOUND,
            "поле терминалов не найдено (label/placeholder «Привязан к терминалу» / «Партнеры»)",
            retryable=True,
        )
    if len(candidates) > 1:
        sources = ", ".join(f"{src}:{name}" for _loc, src, name in candidates)
        log.error(
            "[Partners] stage=find code=%s matches=%s",
            FAIL_TERMINAL_FIELD_AMBIGUOUS,
            sources,
        )
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_AMBIGUOUS,
            f"найдено несколько полей терминалов ({sources})",
            retryable=False,
        )

    loc, source, name = candidates[0]
    log.info("[Partners] stage=find source=%s name=%s", source, name)
    chip_pairs = _wait_field_loaded(loc)
    loaded_empty = len(chip_pairs) == 0
    if loaded_empty:
        log.info("[Partners] stage=read loaded_empty=true chips=0")
    else:
        log.info(
            "[Partners] stage=read loaded_empty=false chips=%s",
            [text for _chip, text in chip_pairs],
        )
    return TerminalField(
        multiselect=loc,
        source=source,
        matched_name=name,
        chip_pairs=chip_pairs,
        loaded_empty=loaded_empty,
    )


def _wrapper_owned_by(wrap: Any, root_handle: Any) -> bool:
    if root_handle is None:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_LOADED,
            "не удалось подтвердить принадлежность списка терминалов",
            retryable=True,
        )
    try:
        return bool(wrap.evaluate("(el, root) => root.contains(el)", root_handle))
    except Exception as exc:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_LOADED,
            "не удалось подтвердить принадлежность списка терминалов",
            retryable=True,
        ) from exc


def _visible_owned_wrappers(multiselect: Any, page: Page, root_handle: Any) -> list[Any]:
    owned: list[Any] = []
    inner = multiselect.locator(".multiselect__content-wrapper")
    inner_count = int(
        _observe(
            lambda: inner.count(),
            "не удалось подтвердить принадлежность списка терминалов",
        )
        or 0
    )
    for i in range(inner_count):
        wrap = inner.nth(i)
        visible = _observe_bool(
            lambda wrap=wrap: wrap.is_visible(),
            "не удалось подтвердить принадлежность списка терминалов",
        )
        if visible:
            owned.append(wrap)
    if owned:
        return owned

    visible = page.locator(".multiselect__content-wrapper:visible")
    visible_count = int(
        _observe(
            lambda: visible.count(),
            "не удалось подтвердить принадлежность списка терминалов",
        )
        or 0
    )
    for i in range(visible_count):
        wrap = visible.nth(i)
        if _wrapper_owned_by(wrap, root_handle):
            owned.append(wrap)
    return owned


def _open_dropdown(page: Page, multiselect: Any) -> Any:
    try:
        multiselect.scroll_into_view_if_needed(timeout=5000)
    except Exception as exc:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_LOADED,
            "не удалось показать поле терминалов",
            retryable=True,
        ) from exc
    try:
        multiselect.click(timeout=3000)
    except Exception:
        log.warning("[Partners] stage=dropdown click failed → force")
        multiselect.click(force=True, timeout=3000)

    handle = None
    try:
        handle = multiselect.element_handle()
    except Exception as exc:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_LOADED,
            "не удалось подтвердить принадлежность списка терминалов",
            retryable=True,
        ) from exc
    if handle is None:
        raise TerminalFieldError(
            FAIL_TERMINAL_FIELD_NOT_LOADED,
            "не удалось подтвердить принадлежность списка терминалов",
            retryable=True,
        )

    deadline = time.monotonic() + (_DROPDOWN_WAIT_MS / 1000.0)
    while True:
        owned = _visible_owned_wrappers(multiselect, page, handle)
        if len(owned) == 1:
            return owned[0]
        if len(owned) > 1:
            raise TerminalFieldError(
                FAIL_TERMINAL_FIELD_AMBIGUOUS,
                "неоднозначный выпадающий список терминалов",
                retryable=False,
            )
        if time.monotonic() >= deadline:
            raise TerminalFieldError(
                FAIL_TERMINAL_FIELD_NOT_LOADED,
                "список терминалов не открылся или не принадлежит полю",
                retryable=True,
            )
        page.wait_for_timeout(100)


def add_terminal_option(page: Page, field: TerminalField, partner: str) -> str:
    """Select ``partner`` from the resolved field. Exact option match only."""
    dropdown = _open_dropdown(page, field.multiselect)
    options = dropdown.locator("li")
    log.info("[Partners] stage=options search partner=%s", partner)

    found = None
    last_count = -1
    for _ in range(25):
        visible_count = options.count()
        log.info("[Partners] stage=options visible=%s", visible_count)
        for i in range(visible_count):
            option = options.nth(i)
            text = option.inner_text().strip()
            if partner_text_matches(text, partner):
                found = option
                log.info("[Partners] stage=options exact_hit text=%s", text)
                break
        if found:
            break
        if visible_count == last_count:
            break
        last_count = visible_count
        try:
            dropdown.evaluate("el => el.scrollTop = el.scrollHeight")
        except Exception:
            break
        page.wait_for_timeout(300)

    if found is None:
        log.error(
            "[Partners] stage=options code=%s partner=%s",
            FAIL_PARTNER_OPTION_NOT_FOUND,
            partner,
        )
        return FAIL_PARTNER_OPTION_NOT_FOUND

    try:
        found.scroll_into_view_if_needed(timeout=3000)
        found.click(timeout=3000)
    except Exception:
        log.warning("[Partners] stage=options click failed → force")
        found.click(force=True, timeout=3000)

    deadline = time.monotonic() + 2.0
    while True:
        refreshed = _wait_field_loaded(field.multiselect)
        if any(partner_text_matches(text, partner) for _chip, text in refreshed):
            log.info("[Partners] stage=add confirmed partner=%s", partner)
            return "added"
        if time.monotonic() >= deadline:
            break
        page.wait_for_timeout(50)
    log.error(
        "[Partners] stage=add code=%s partner=%s",
        FAIL_ADD_NOT_CONFIRMED,
        partner,
    )
    return FAIL_ADD_NOT_CONFIRMED
