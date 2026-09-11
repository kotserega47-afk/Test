"""Terminal/partner field lookup, empty vs unread, add/remove, dependent set_status."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.engine import (
    RESULT_OK_SET_AGGREGATE,
    RESULT_STOP_BEFORE_SAVE,
    _order_card_actions,
    _status_from_action_result,
    ensure_partner_added,
    ensure_partner_removed,
    get_partner_chips,
    run,
)
from automation.runtime import RunConfig
from automation.wallet_terminal_field import (
    FAIL_PARTNER_OPTION_NOT_FOUND,
    FAIL_SAVE_NOT_CONFIRMED,
    FAIL_TERMINAL_FIELD_AMBIGUOUS,
    FAIL_TERMINAL_FIELD_NOT_FOUND,
    FAIL_TERMINAL_FIELD_NOT_LOADED,
    SKIP_BLOCKED_BY_ADD_FAILURE,
    TERMINAL_FIELD_NAMES,
    TerminalFieldError,
    _wait_field_loaded,
    partner_text_matches,
    resolve_terminal_field,
)
from integrations.wallet_editor_hold import HoldPairsSnapshot

PARTNER_150 = "ESB Юмани с2с (150)"
PARTNER_157 = "BR Affor Тбанк ABHsber (157)"


def _cfg(result_path: str) -> RunConfig:
    return RunConfig(
        login="u",
        password="p",
        result_file_path=result_path,
        retries=1,
        delay=0,
        headless=True,
    )


def _hold_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        "automation.engine.load_hold_pairs_snapshot",
        lambda: HoldPairsSnapshot(available=True, pairs=set(), error=None),
    )


def _playwright_stack(monkeypatch, *, patch_verify: bool = True) -> MagicMock:
    page = MagicMock()
    context = MagicMock()
    browser = MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    playwright = MagicMock()
    playwright.chromium.launch.return_value = browser
    cm = MagicMock()
    cm.__enter__.return_value = playwright
    cm.__exit__.return_value = False
    monkeypatch.setattr("automation.engine.sync_playwright", lambda: cm)
    monkeypatch.setattr("automation.engine._ensure_logged_in", MagicMock())
    monkeypatch.setattr(
        "automation.engine.require_wallet_editor_antares_credentials",
        MagicMock(),
    )
    monkeypatch.setattr("automation.engine.retry", lambda fn, *a, **k: fn())
    monkeypatch.setattr(
        "automation.engine._apply_auto_no_partners_status_after_actions",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr("automation.engine.close_playwright_stack", MagicMock())
    monkeypatch.setattr("automation.engine.ensure_wallet_search_ready", MagicMock(return_value=True))
    monkeypatch.setattr("automation.engine._close_stale_modal", MagicMock())
    if patch_verify:
        monkeypatch.setattr(
            "automation.engine._verify_card_enable_after_save",
            MagicMock(return_value=None),
        )
    return page


def _schedule_loading_settle(page, selector: str = "#terminal-field", hold_ms: int = 200) -> None:
    page.evaluate(
        """([sel, ms]) => {
          const el = document.querySelector(sel);
          if (!el) return;
          el.classList.add("multiselect--loading");
          setTimeout(() => el.classList.remove("multiselect--loading"), ms);
        }""",
        [selector, hold_ms],
    )


def _multiselect_html(
    *,
    label: str,
    placeholder: str = "",
    chips: tuple[str, ...] = (),
    options: tuple[str, ...] = (),
    include_tags: bool = True,
    extra_group: bool = True,
) -> str:
    chip_html = "".join(
        f'<span class="multiselect__tag">{name}'
        f'<i class="multiselect__tag-icon" onclick="this.closest(\'.multiselect__tag\').remove(); event.stopPropagation();"></i>'
        f"</span>"
        for name in chips
    )
    option_html = "".join(
        f'<li onclick="window.__weAddChip && window.__weAddChip(this); event.stopPropagation();">{name}</li>'
        for name in options
    )
    tags = (
        f'<div class="multiselect__tags">{chip_html}'
        f'<span class="multiselect__placeholder">Выбрать</span>'
        f'<input class="multiselect__input" placeholder="{placeholder}" />'
        f"</div>"
        if include_tags
        else ""
    )
    group = ""
    if extra_group:
        group = """
        <div class="row">
          <label>Группа</label>
          <div class="multiselect">
            <div class="multiselect__tags">
              <span class="multiselect__tag">Other group</span>
              <input class="multiselect__input" placeholder="Группа" />
            </div>
          </div>
        </div>
        """
    return f"""
    <style>.multiselect__tag-icon {{ display:inline-block; width:12px; height:12px; }}</style>
    <div id="wallet-add-modal___BV_modal_body_">
      {group}
      <div class="row">
        <label>{label}</label>
        <div class="multiselect" id="terminal-field">
          {tags}
          <div class="multiselect__content-wrapper">
            <ul>{option_html}</ul>
          </div>
        </div>
      </div>
    </div>
    <script>
      window.__weAddChip = (li) => {{
        const root = document.getElementById("terminal-field");
        if (!root || !li) return;
        const text = (li.innerText || "").trim();
        const tags = root.querySelector(".multiselect__tags");
        if (!tags || !text) return;
        const already = Array.from(tags.querySelectorAll(".multiselect__tag"))
          .some((el) => (el.innerText || "").trim() === text);
        if (already) return;
        const span = document.createElement("span");
        span.className = "multiselect__tag";
        span.appendChild(document.createTextNode(text));
        const icon = document.createElement("i");
        icon.className = "multiselect__tag-icon";
        icon.addEventListener("click", (ev) => {{
          span.remove();
          ev.preventDefault();
          ev.stopPropagation();
        }});
        span.appendChild(icon);
        tags.prepend(span);
      }};
      const root = document.getElementById("terminal-field");
      if (root) {{
        root.addEventListener("click", (ev) => {{
          const icon = ev.target.closest(".multiselect__tag-icon");
          if (icon) {{
            const tag = icon.closest(".multiselect__tag");
            if (tag) tag.remove();
            ev.preventDefault();
            ev.stopPropagation();
          }}
        }});
      }}
    </script>
    """


@pytest.fixture(scope="module")
def browser_page():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            yield page
            browser.close()
    except Exception as exc:
        pytest.skip(f"playwright chromium unavailable: {exc}")


def test_terminal_names_include_new_and_old():
    assert "Привязан к терминалу" in TERMINAL_FIELD_NAMES
    assert "Партнеры" in TERMINAL_FIELD_NAMES


def test_partner_150_does_not_match_157():
    assert partner_text_matches(PARTNER_150, PARTNER_150) is True
    assert partner_text_matches(PARTNER_157, PARTNER_150) is False
    assert partner_text_matches("xx ESB Юмани с2с (150) yy", PARTNER_150) is False


def test_fail_codes_never_map_to_ok():
    assert _status_from_action_result(FAIL_TERMINAL_FIELD_NOT_FOUND) == FAIL_TERMINAL_FIELD_NOT_FOUND
    assert _status_from_action_result(FAIL_PARTNER_OPTION_NOT_FOUND) == FAIL_PARTNER_OPTION_NOT_FOUND
    assert _status_from_action_result("skip: not selected") == "SKIP"
    assert _status_from_action_result("added") == "OK"


def test_order_puts_add_partner_before_set_status():
    ordered = _order_card_actions(
        [(0, "set_status", "Готов к работе"), (1, "add_partner", PARTNER_150)]
    )
    assert [item[1] for item in ordered] == ["add_partner", "set_status"]


def test_new_label_markup(browser_page):
    browser_page.set_content(
        _multiselect_html(
            label="Привязан к терминалу",
            chips=(PARTNER_150,),
            options=(PARTNER_150, PARTNER_157),
        )
    )
    field = resolve_terminal_field(browser_page)
    assert field.matched_name == "Привязан к терминалу"
    assert field.source == "label"
    assert [text for _, text in field.chip_pairs] == [PARTNER_150]
    assert field.loaded_empty is False


def test_old_placeholder_markup(browser_page):
    html = _multiselect_html(
        label="Другое",
        placeholder="Партнеры",
        chips=(),
        extra_group=False,
    ).replace("<label>Другое</label>", "")
    browser_page.set_content(html)
    _schedule_loading_settle(browser_page)
    field = resolve_terminal_field(browser_page)
    assert field.matched_name == "Партнеры"
    assert field.source == "placeholder"
    assert field.loaded_empty is True


def test_chooses_terminal_not_group_multiselect(browser_page):
    browser_page.set_content(
        _multiselect_html(label="Привязан к терминалу", chips=(PARTNER_150,), extra_group=True)
    )
    chips = get_partner_chips(browser_page)
    assert [text for _, text in chips] == [PARTNER_150]


def test_missing_field_fails_not_empty_list(browser_page):
    browser_page.set_content(
        """
        <div id="wallet-add-modal___BV_modal_body_">
          <div class="row">
            <label>Группа</label>
            <div class="multiselect">
              <div class="multiselect__tags">
                <input class="multiselect__input" placeholder="Группа" />
              </div>
            </div>
          </div>
        </div>
        """
    )
    with pytest.raises(Exception) as exc:
        get_partner_chips(browser_page)
    assert getattr(exc.value, "code", "") == FAIL_TERMINAL_FIELD_NOT_FOUND
    result = ensure_partner_removed(browser_page, PARTNER_150, RunConfig())
    assert result == FAIL_TERMINAL_FIELD_NOT_FOUND
    assert result != "skip: not selected"


def test_ambiguous_two_terminal_fields(browser_page):
    browser_page.set_content(
        """
        <div id="wallet-add-modal___BV_modal_body_">
          <div class="row">
            <label>Привязан к терминалу</label>
            <div class="multiselect"><div class="multiselect__tags"></div></div>
          </div>
          <div class="row">
            <label>Партнеры</label>
            <div class="multiselect"><div class="multiselect__tags"></div></div>
          </div>
        </div>
        """
    )
    with pytest.raises(Exception) as exc:
        resolve_terminal_field(browser_page)
    assert getattr(exc.value, "code", "") == FAIL_TERMINAL_FIELD_AMBIGUOUS


def test_delayed_then_empty_field(browser_page):
    browser_page.set_content(
        """
        <div id="wallet-add-modal___BV_modal_body_">
          <div class="row">
            <label>Привязан к терминалу</label>
            <div class="multiselect" id="delayed"></div>
          </div>
        </div>
        """
    )
    browser_page.evaluate(
        """
        () => {
          const el = document.getElementById("delayed");
          el.classList.add("multiselect--loading");
          setTimeout(() => {
            el.innerHTML = '<div class="multiselect__tags">'
              + '<span class="multiselect__placeholder">Выбрать</span>'
              + '<input class="multiselect__input" />'
              + '</div>';
          }, 400);
        }
        """
    )
    _schedule_loading_settle(browser_page, "#delayed", hold_ms=1500)
    field = resolve_terminal_field(browser_page)
    assert field.loaded_empty is True
    assert field.chip_pairs == []


def test_field_not_loaded(browser_page):
    browser_page.set_content(
        """
        <div id="wallet-add-modal___BV_modal_body_">
          <div class="row">
            <label>Привязан к терминалу</label>
            <div class="multiselect"></div>
          </div>
        </div>
        """
    )
    with pytest.raises(Exception) as exc:
        resolve_terminal_field(browser_page)
    assert getattr(exc.value, "code", "") == FAIL_TERMINAL_FIELD_NOT_LOADED


def test_add_and_remove_partner_150(browser_page):
    browser_page.set_content(
        _multiselect_html(
            label="Привязан к терминалу",
            chips=(PARTNER_157,),
            options=(PARTNER_150, PARTNER_157),
            extra_group=True,
        )
    )
    added = ensure_partner_added(browser_page, PARTNER_150, RunConfig())
    assert added == "added"
    chips = [text for _, text in get_partner_chips(browser_page)]
    assert PARTNER_150 in chips
    assert PARTNER_157 in chips
    removed = ensure_partner_removed(browser_page, PARTNER_150, RunConfig())
    assert removed == "removed"
    chips = [text for _, text in get_partner_chips(browser_page)]
    assert PARTNER_150 not in chips
    assert PARTNER_157 in chips


def test_add_does_not_pick_157_for_150(browser_page):
    browser_page.set_content(
        _multiselect_html(
            label="Привязан к терминалу",
            chips=(),
            options=(PARTNER_157,),
        )
    )
    _schedule_loading_settle(browser_page)
    result = ensure_partner_added(browser_page, PARTNER_150, RunConfig())
    assert result == FAIL_PARTNER_OPTION_NOT_FOUND


def test_add_failure_blocks_dependent_set_status_and_save(
    tmp_path, monkeypatch
):
    _hold_ok(monkeypatch)
    page = _playwright_stack(monkeypatch)
    opens: list[str] = []
    saves = MagicMock(return_value="saved")
    statuses: list[str] = []

    def open_card(_page, card):
        opens.append(card)

    monkeypatch.setattr("automation.engine.open_card", open_card)
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value=FAIL_TERMINAL_FIELD_NOT_FOUND),
    )
    monkeypatch.setattr(
        "automation.engine.ensure_status_set",
        lambda *_a, **_k: statuses.append("ran") or "set: Готов к работе",
    )
    monkeypatch.setattr("automation.engine.save", saves)

    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["set_status", "add_partner"],
            "value": ["Готов к работе", PARTNER_150],
        }
    ).to_excel(path, index=False)
    out, _stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    add_row = df[df["action"] == "add_partner"].iloc[0]
    status_row = df[df["action"] == "set_status"].iloc[0]
    assert add_row["status"] == FAIL_TERMINAL_FIELD_NOT_FOUND
    assert status_row["status"] == "SKIP"
    assert SKIP_BLOCKED_BY_ADD_FAILURE in str(status_row["comment"])
    assert statuses == []
    saves.assert_not_called()


def test_standalone_set_status_still_runs(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch)
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    set_status = MagicMock(return_value="set: Готов к работе")
    monkeypatch.setattr("automation.engine.ensure_status_set", set_status)
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["set_status"],
            "value": ["Готов к работе"],
        }
    ).to_excel(path, index=False)
    out, _stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert list(df["status"]) == ["OK"]
    set_status.assert_called_once()


def test_next_card_opens_after_add_failure(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(
        "automation.engine.open_card",
        lambda _p, card: opened.append(card),
    )
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(side_effect=[FAIL_TERMINAL_FIELD_NOT_FOUND, "added"]),
    )
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111", "4222222222222222"],
            "action": ["add_partner", "add_partner"],
            "value": [PARTNER_150, PARTNER_150],
        }
    ).to_excel(path, index=False)
    out, _stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert list(df["status"]) == [FAIL_TERMINAL_FIELD_NOT_FOUND, "OK"]
    assert opened == ["4111111111111111", "4222222222222222"]


def test_verify_save_failure_downgrades_ok(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch)
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value="added"),
    )
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))
    monkeypatch.setattr(
        "automation.engine._verify_card_enable_after_save",
        MagicMock(return_value=FAIL_SAVE_NOT_CONFIRMED),
    )
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["add_partner"],
            "value": [PARTNER_150],
        }
    ).to_excel(path, index=False)
    out, _stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert df.iloc[0]["status"] == FAIL_SAVE_NOT_CONFIRMED


def test_already_added_allows_set_status(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch)
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value=f"skip: partner already added: {PARTNER_150}"),
    )
    set_status = MagicMock(return_value="set: Готов к работе")
    monkeypatch.setattr("automation.engine.ensure_status_set", set_status)
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["add_partner", "set_status"],
            "value": [PARTNER_150, "Готов к работе"],
        }
    ).to_excel(path, index=False)
    out, _stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert list(df["status"]) == ["SKIP", "OK"]
    set_status.assert_called_once()


def _mock_terminal_multiselect(
    *,
    spinner_error: Exception | None = None,
    chip_texts: tuple[str, ...] = (),
    unread_index: int | None = None,
    placeholder_visible: bool = True,
):
    tags = MagicMock()
    tags.first.wait_for = MagicMock()
    spinner = MagicMock()
    if spinner_error is not None:
        spinner.count.side_effect = spinner_error
    else:
        spinner.count.return_value = 0
        spinner.first.is_visible.return_value = False
    chips = MagicMock()
    chips.count.return_value = len(chip_texts)
    chip_locators = []
    for i, text in enumerate(chip_texts):
        chip = MagicMock()
        chip.is_visible.return_value = True
        if unread_index is not None and i == unread_index:
            chip.inner_text.side_effect = RuntimeError("chip detached")
        else:
            chip.inner_text.return_value = text
        chip_locators.append(chip)
    chips.nth.side_effect = lambda i: chip_locators[i]
    placeholder = MagicMock()
    placeholder.count.return_value = 1 if placeholder_visible else 0
    placeholder.first.is_visible.return_value = placeholder_visible
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    multi = MagicMock()
    multi.page = page
    multi.get_attribute.return_value = ""

    def locator(selector: str):
        if selector == ".multiselect__tags":
            return tags
        if "spinner" in selector or "loading" in selector:
            return spinner
        if selector == ".multiselect__tag":
            return chips
        if "placeholder" in selector:
            return placeholder
        return MagicMock()

    multi.locator.side_effect = locator
    return multi


def test_shell_visible_chips_appear_later_than_empty_confirm_window(browser_page):
    browser_page.set_content(
        """
        <div id="wallet-add-modal___BV_modal_body_">
          <div class="row">
            <label>Привязан к терминалу</label>
            <div class="multiselect" id="delayed">
              <div class="multiselect__tags">
                <span class="multiselect__placeholder">Выбрать</span>
                <input class="multiselect__input" />
              </div>
            </div>
          </div>
        </div>
        """
    )
    browser_page.evaluate(
        f"""
        () => setTimeout(() => {{
          const tags = document.querySelector("#delayed .multiselect__tags");
          const span = document.createElement("span");
          span.className = "multiselect__tag";
          span.textContent = {PARTNER_150!r};
          tags.prepend(span);
        }}, 2000)
        """
    )
    field = resolve_terminal_field(browser_page)
    assert field.loaded_empty is False
    assert [text for _, text in field.chip_pairs] == [PARTNER_150]


def test_unread_chip_is_technical_fail_not_partial():
    multi = _mock_terminal_multiselect(
        chip_texts=(PARTNER_150, PARTNER_157),
        unread_index=1,
    )
    with pytest.raises(TerminalFieldError) as exc:
        _wait_field_loaded(multi)
    assert exc.value.code == FAIL_TERMINAL_FIELD_NOT_LOADED


def test_loading_state_check_failure_is_not_empty():
    multi = _mock_terminal_multiselect()
    multi.get_attribute.side_effect = RuntimeError("cannot read class")
    with pytest.raises(TerminalFieldError) as exc:
        _wait_field_loaded(multi)
    assert exc.value.code == FAIL_TERMINAL_FIELD_NOT_LOADED


def test_genuinely_loaded_empty_field(browser_page):
    browser_page.set_content(
        _multiselect_html(
            label="Привязан к терминалу",
            chips=(),
            extra_group=False,
        )
    )
    _schedule_loading_settle(browser_page)
    field = resolve_terminal_field(browser_page)
    assert field.loaded_empty is True
    assert field.chip_pairs == []


def test_second_add_failure_does_not_leave_first_ok(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch)
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(side_effect=["added", FAIL_PARTNER_OPTION_NOT_FOUND]),
    )
    saves = MagicMock(return_value="saved")
    monkeypatch.setattr("automation.engine.save", saves)
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["add_partner", "add_partner"],
            "value": [PARTNER_150, PARTNER_157],
        }
    ).to_excel(path, index=False)
    out, stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert df.iloc[0]["status"] == RESULT_STOP_BEFORE_SAVE
    assert df.iloc[1]["status"] == FAIL_PARTNER_OPTION_NOT_FOUND
    assert "OK" not in [str(value) for value in df["status"]]
    saves.assert_not_called()
    assert stats.ok == 0


def test_verify_reopen_failure_downgrades_ok(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch, patch_verify=False)
    opens: list[str] = []

    def open_card(_page, card):
        opens.append(card)
        if len(opens) > 1:
            raise RuntimeError("verify reopen failed")

    monkeypatch.setattr("automation.engine.open_card", open_card)
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value="added"),
    )
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["add_partner"],
            "value": [PARTNER_150],
        }
    ).to_excel(path, index=False)
    out, stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert df.iloc[0]["status"] == FAIL_SAVE_NOT_CONFIRMED
    assert stats.ok == 0
    assert len(opens) >= 2


def test_verify_status_unreadable_after_save(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch, patch_verify=False)
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value="added"),
    )
    monkeypatch.setattr(
        "automation.engine.ensure_status_set",
        MagicMock(return_value="set: Готов к работе"),
    )
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))
    field = MagicMock()
    field.chip_pairs = [(MagicMock(), PARTNER_150)]
    monkeypatch.setattr("automation.engine.resolve_terminal_field", lambda _p: field)
    monkeypatch.setattr("automation.engine._get_current_card_status", lambda _p: "")
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["add_partner", "set_status"],
            "value": [PARTNER_150, "Готов к работе"],
        }
    ).to_excel(path, index=False)
    out, stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert list(df["status"]) == [FAIL_SAVE_NOT_CONFIRMED, FAIL_SAVE_NOT_CONFIRMED]
    assert stats.ok == 0


def test_verify_partner_missing_after_save(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch, patch_verify=False)
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value="added"),
    )
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))
    field = MagicMock()
    field.chip_pairs = []
    monkeypatch.setattr("automation.engine.resolve_terminal_field", lambda _p: field)
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["add_partner"],
            "value": [PARTNER_150],
        }
    ).to_excel(path, index=False)
    out, stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert df.iloc[0]["status"] == FAIL_SAVE_NOT_CONFIRMED
    assert stats.ok == 0


def test_foreign_dropdown_is_not_clicked(browser_page):
    browser_page.set_content(
        f"""
        <div id="wallet-add-modal___BV_modal_body_">
          <div class="row">
            <label>Группа</label>
            <div class="multiselect" id="group-field">
              <div class="multiselect__tags">
                <input class="multiselect__input" placeholder="Группа" />
              </div>
              <div class="multiselect__content-wrapper" style="display:block">
                <ul><li>{PARTNER_150}</li></ul>
              </div>
            </div>
          </div>
          <div class="row">
            <label>Привязан к терминалу</label>
            <div class="multiselect" id="terminal-field">
              <div class="multiselect__tags">
                <span class="multiselect__placeholder">Выбрать</span>
                <input class="multiselect__input" />
              </div>
              <div class="multiselect__content-wrapper" style="display:none">
                <ul></ul>
              </div>
            </div>
          </div>
        </div>
        """
    )
    _schedule_loading_settle(browser_page)
    result = ensure_partner_added(browser_page, PARTNER_150, RunConfig())
    assert result == FAIL_TERMINAL_FIELD_NOT_LOADED
    texts = browser_page.locator("#terminal-field .multiselect__tag").all_inner_texts()
    assert PARTNER_150 not in [text.strip() for text in texts]


def test_save_exception_downgrades_unconfirmed_ok(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch)
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value="added"),
    )
    monkeypatch.setattr(
        "automation.engine.ensure_status_set",
        MagicMock(return_value="set: Готов к работе"),
    )
    monkeypatch.setattr(
        "automation.engine.save",
        MagicMock(side_effect=RuntimeError("save crashed")),
    )
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["add_partner", "set_status"],
            "value": [PARTNER_150, "Готов к работе"],
        }
    ).to_excel(path, index=False)
    out, stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    assert list(df["status"]) == [FAIL_SAVE_NOT_CONFIRMED, FAIL_SAVE_NOT_CONFIRMED]
    assert stats.ok == 0


def test_aggregate_ok_but_partner_missing_after_save(tmp_path, monkeypatch):
    _hold_ok(monkeypatch)
    _playwright_stack(monkeypatch, patch_verify=False)
    monkeypatch.setattr(
        "automation.engine.find_and_open_card_for_delete",
        MagicMock(return_value=0),
    )
    monkeypatch.setattr(
        "automation.engine.apply_set_aggregate_on_open_form",
        MagicMock(return_value=(None, "79001112233")),
    )
    monkeypatch.setattr(
        "automation.engine.save_shared_wallet_form",
        MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        "automation.engine.verify_set_aggregate_after_save",
        MagicMock(return_value=RESULT_OK_SET_AGGREGATE),
    )
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr(
        "automation.engine.ensure_partner_added",
        MagicMock(return_value="added"),
    )
    field = MagicMock()
    field.chip_pairs = []
    monkeypatch.setattr("automation.engine.resolve_terminal_field", lambda _p: field)
    path = tmp_path / "in.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["set_aggregate", "add_partner"],
            "value": ["Sim A (1)", PARTNER_150],
        }
    ).to_excel(path, index=False)
    out, _stats = run(str(path), _cfg(str(tmp_path / "out.xlsx")))
    df = pd.read_excel(out)
    agg_row = df[df["action"] == "set_aggregate"].iloc[0]
    add_row = df[df["action"] == "add_partner"].iloc[0]
    assert agg_row["status"] == RESULT_OK_SET_AGGREGATE
    assert add_row["status"] == FAIL_SAVE_NOT_CONFIRMED
    assert add_row["status"] != "OK"

