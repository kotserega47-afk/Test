"""Unit tests for Wallet Editor action=set_aggregate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.add_wallet_engine import SaveWaitOutcome
from automation.engine import (
    ALLOWED_ACTIONS,
    RESULT_FAIL_SET_AGGREGATE_CONFLICT,
    RESULT_OK_SET_AGGREGATE,
    RESULT_SKIP_NOT_FOUND,
    RESULT_STOP_BEFORE_SAVE,
    _prepare_df,
    _status_from_set_aggregate_result,
    _validate_set_aggregate_conflicts,
    _validate_set_direction_pre_playwright,
    timing_outcome_from_result,
)
from automation.runtime import RunConfig
from automation.set_aggregate_engine import (
    RESULT_FAIL_AGGREGATE_FIELDS,
    RESULT_FAIL_AGGREGATE_NOT_FOUND,
    RESULT_FAIL_AGGREGATE_SWITCH,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_VALIDATION,
    RESULT_FAIL_VERIFY,
    AggregateSwitchError,
    SetAggregateIntent,
    active_aggregate_names,
    apply_set_aggregate_on_open_form,
    ensure_set_aggregate,
    fill_set_aggregate_nested_fields,
    intent_from_excel_row,
    present_set_aggregate_optional_columns,
    switch_to_single_aggregate,
    target_is_sole_active,
    verify_set_aggregate,
)


def _cfg(**kwargs) -> RunConfig:
    defaults = {
        "login": "u",
        "password": "p",
        "headless": True,
        "dry_run": False,
        "stop_before_save": False,
        "stop_before_save_pause_ms": 0,
    }
    defaults.update(kwargs)
    return RunConfig(**defaults)


def _intent(**kwargs) -> SetAggregateIntent:
    defaults = {
        "card": "9990080812345678",
        "aggregate": "ЧБР",
    }
    defaults.update(kwargs)
    return SetAggregateIntent(**defaults)


def _checkbox_label(name: str, *, checked: bool):
    label = MagicMock()
    label.inner_text.return_value = name
    cb = MagicMock()
    cb.count.return_value = 1
    cb.first.is_checked.return_value = checked
    cb.first.check = MagicMock()
    cb.first.uncheck = MagicMock()
    return label, cb


def test_set_aggregate_is_allowed_action():
    assert "set_aggregate" in ALLOWED_ACTIONS


def test_prepare_df_accepts_set_aggregate_and_optional_columns(tmp_path):
    path = tmp_path / "set_agg.xlsx"
    pd.DataFrame(
        {
            "card": ["9990080812345678"],
            "action": ["SET_AGGREGATE"],
            "value": ["ЧБР"],
            "phone": ["998901234567"],
            "account": ["user123"],
            "merchant_id_sbp": ["456789"],
            "account_number": ["40820810000000007380"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "set_aggregate"
    assert df.iloc[0]["value"] == "ЧБР"
    assert df.iloc[0]["phone"] == "998901234567"
    assert "phone" in df.attrs["set_aggregate_optional_columns"]
    assert "account" in df.attrs["set_aggregate_optional_columns"]


def test_empty_set_aggregate_value_pre_playwright_fail():
    df = pd.DataFrame(
        {
            "card": ["9990080812345678"],
            "action": ["set_aggregate"],
            "value": [""],
            "status": [""],
            "comment": [""],
        }
    )
    assert _validate_set_direction_pre_playwright(df) == 1
    assert df.iloc[0]["status"] == "FAIL"
    assert "set_aggregate" in df.iloc[0]["comment"]


def test_set_aggregate_conflict_with_other_action():
    df = pd.DataFrame(
        {
            "card": ["111", "111"],
            "action": ["set_aggregate", "set_status"],
            "value": ["ЧБР", "Тест"],
            "status": ["", ""],
            "comment": ["", ""],
        }
    )
    assert _validate_set_aggregate_conflicts(df) == 2
    assert (df["status"] == RESULT_FAIL_SET_AGGREGATE_CONFLICT).all()


def test_intent_optional_columns_absent_vs_present():
    row = pd.Series(
        {
            "card": "9990080812345678",
            "action": "set_aggregate",
            "value": "ЧБР",
            "phone": "998901234567",
        }
    )
    intent = intent_from_excel_row(
        card="9990080812345678",
        aggregate="ЧБР",
        row=row,
        present_optional=frozenset({"phone"}),
        columns=row.index,
    )
    assert intent.phone == "998901234567"
    assert intent.account is None
    assert intent.provided_optional() == {"phone": "998901234567"}

    intent2 = intent_from_excel_row(
        card="9990080812345678",
        aggregate="ЧБР",
        row=row,
        present_optional=frozenset(),
        columns=row.index,
    )
    assert intent2.phone is None
    assert intent2.provided_optional() == {}


def test_present_optional_aliases():
    cols = present_set_aggregate_optional_columns(
        ["card", "action", "value", "Телефон", "Аккаунт"]
    )
    assert cols == frozenset({"phone", "account"})


class _DelayedAggUI:
    """Simulate Antares: delayed is_checked + DOM recreate after each toggle."""

    def __init__(self, initial: dict[str, bool], *, delay_reads: int = 2):
        self.state = dict(initial)
        self.delay_reads = delay_reads
        self._pending: dict[str, tuple[bool, int]] = {}
        self.generations: dict[str, int] = {k: 0 for k in initial}
        self.stale_hits = 0
        self.checkboxes: dict[str, MagicMock] = {}
        self.labels: dict[str, MagicMock] = {}
        for name in initial:
            self._rebuild_checkbox(name)

    def _rebuild_checkbox(self, name: str) -> MagicMock:
        gen = self.generations[name]
        cb_first = MagicMock(name=f"cb:{name}:gen{gen}")
        cb_wrap = MagicMock()
        cb_wrap.count.return_value = 1
        cb_wrap.first = cb_first
        label = MagicMock(name=f"label:{name}:gen{gen}")
        label.inner_text.return_value = name
        self.bind_checkbox_methods(name, cb_first, gen)
        self.checkboxes[name] = cb_wrap
        self.labels[name] = label
        return cb_wrap

    def tick(self) -> None:
        """Advance pending delays one step (called from page.wait_for_timeout)."""
        for name in list(self._pending.keys()):
            self._advance_pending(name)

    def _advance_pending(self, name: str) -> bool:
        pending = self._pending.get(name)
        if pending is None:
            return self.state[name]
        want, left = pending
        if left > 0:
            self._pending[name] = (want, left - 1)
            return not want
        self.state[name] = want
        del self._pending[name]
        return self.state[name]

    def snapshot(self) -> list[tuple[str, bool]]:
        """Peek current visible states; commit when delay counter reaches zero."""
        out: list[tuple[str, bool]] = []
        for name in list(self.state.keys()):
            pending = self._pending.get(name)
            if pending is None:
                out.append((name, self.state[name]))
                continue
            want, left = pending
            if left > 0:
                out.append((name, not want))
            else:
                self.state[name] = want
                del self._pending[name]
                out.append((name, want))
        return out

    def find_label(self, modal, name: str):
        return self.labels.get(name)

    def checkbox_for_label(self, label_el):
        for name, lab in self.labels.items():
            if lab is label_el:
                return self.checkboxes[name]
        return MagicMock(count=MagicMock(return_value=0))

    def bind_checkbox_methods(self, name: str, cb_first: MagicMock, gen: int) -> None:
        def is_checked(_self=None, *, _name=name, _gen=gen):
            if self.generations[_name] != _gen:
                self.stale_hits += 1
                raise Exception("stale checkbox locator")
            return dict(self.snapshot())[_name]

        def uncheck(force=True, *, _name=name, _gen=gen):
            if self.generations[_name] != _gen:
                self.stale_hits += 1
                raise Exception("stale checkbox locator")
            self._pending[_name] = (False, self.delay_reads)
            self.generations[_name] += 1
            self._rebuild_checkbox(_name)

        def check(force=True, *, _name=name, _gen=gen):
            if self.generations[_name] != _gen:
                self.stale_hits += 1
                raise Exception("stale checkbox locator")
            self._pending[_name] = (True, self.delay_reads)
            self.generations[_name] += 1
            self._rebuild_checkbox(_name)

        cb_first.is_checked.side_effect = is_checked
        cb_first.uncheck.side_effect = uncheck
        cb_first.check.side_effect = check


def _patch_delayed_switch(ui: _DelayedAggUI, page: MagicMock):
    aw = MagicMock()
    aw.MODAL_BODY = "#modal"
    page.locator.return_value = MagicMock()
    page.wait_for_timeout.side_effect = lambda *_a, **_k: ui.tick()
    aw.checkbox_label_matches.side_effect = (
        lambda text, name: text.strip() == name.strip()
    )
    aw._find_aggregate_checkbox_label.side_effect = ui.find_label
    aw._checkbox_for_label.side_effect = ui.checkbox_for_label
    return aw


def _switch_patches(ui: _DelayedAggUI, aw):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "automation.set_aggregate_engine.list_aggregate_checkbox_states",
            side_effect=lambda p: ui.snapshot(),
        )
    )
    stack.enter_context(
        patch(
            "automation.set_aggregate_engine.find_aggregate_state",
            side_effect=lambda p, name: dict(ui.snapshot()).get(name),
        )
    )
    stack.enter_context(
        patch(
            "automation.set_aggregate_engine.active_aggregate_names",
            side_effect=lambda p: [n for n, c in ui.snapshot() if c],
        )
    )
    stack.enter_context(
        patch(
            "automation.set_aggregate_engine.target_is_sole_active",
            side_effect=lambda p, name: [n for n, c in ui.snapshot() if c] == [name],
        )
    )
    stack.enter_context(
        patch("automation.set_aggregate_engine._aw", return_value=aw)
    )
    stack.enter_context(
        patch(
            "automation.set_aggregate_engine._fresh_aggregate_checkbox",
            side_effect=lambda p, name: (
                ui.checkboxes[name].first if name in ui.checkboxes else None
            ),
        )
    )
    stack.enter_context(
        patch("automation.set_aggregate_engine._AGGREGATE_TOGGLE_POLL_MS", 1)
    )
    stack.enter_context(
        patch("automation.set_aggregate_engine._AGGREGATE_TOGGLE_TIMEOUT_MS", 2000)
    )
    return stack



def test_switch_from_old_to_new_aggregate_waits_for_delayed_checked():
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    ui = _DelayedAggUI({"Тинькофф АПК": True, "ЧБР": False}, delay_reads=2)
    aw = _patch_delayed_switch(ui, page)

    with _switch_patches(ui, aw):
        switch_to_single_aggregate(page, "ЧБР")

    assert ui.state["Тинькофф АПК"] is False
    assert ui.state["ЧБР"] is True
    assert page.wait_for_timeout.called


def test_switch_unchecks_multiple_active_aggregates_with_delay():
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    ui = _DelayedAggUI({"A": True, "B": True, "ЧБР": False}, delay_reads=1)
    aw = _patch_delayed_switch(ui, page)

    with _switch_patches(ui, aw):
        switch_to_single_aggregate(page, "ЧБР")

    assert ui.state == {"A": False, "B": False, "ЧБР": True}


def test_dom_recreate_invalidates_old_locator_during_switch():
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    ui = _DelayedAggUI({"Old": True, "Sim A (1)": False}, delay_reads=1)
    aw = _patch_delayed_switch(ui, page)
    old_gen_before = ui.generations["Old"]

    with _switch_patches(ui, aw):
        switch_to_single_aggregate(page, "Sim A (1)")

    assert ui.generations["Old"] > old_gen_before
    assert ui.generations["Sim A (1)"] > 0
    assert ui.state["Sim A (1)"] is True


def test_switch_timeout_returns_fail_aggregate_switch():
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    # Target never becomes checked → timeout
    ui = _DelayedAggUI({"Old": True, "Sim A (1)": False}, delay_reads=50)
    # After uncheck, never apply check settle for target: force find_state stuck
    aw = _patch_delayed_switch(ui, page)

    def stuck_find(p, name):
        snap = dict(ui.snapshot())
        if name == "Sim A (1)":
            return False  # never confirms checked
        return snap.get(name)

    with (
        patch(
            "automation.set_aggregate_engine.list_aggregate_checkbox_states",
            side_effect=lambda p: ui.snapshot(),
        ),
        patch(
            "automation.set_aggregate_engine.find_aggregate_state",
            side_effect=stuck_find,
        ),
        patch(
            "automation.set_aggregate_engine.active_aggregate_names",
            side_effect=lambda p: [n for n, c in ui.snapshot() if c],
        ),
        patch(
            "automation.set_aggregate_engine.target_is_sole_active",
            return_value=False,
        ),
        patch("automation.set_aggregate_engine._aw", return_value=aw),
        patch(
            "automation.set_aggregate_engine._fresh_aggregate_checkbox",
            side_effect=lambda p, name: (
                ui.checkboxes[name].first if name in ui.checkboxes else None
            ),
        ),
        patch("automation.set_aggregate_engine._AGGREGATE_TOGGLE_POLL_MS", 1),
        patch("automation.set_aggregate_engine._AGGREGATE_TOGGLE_TIMEOUT_MS", 30),
    ):
        with pytest.raises(AggregateSwitchError) as exc:
            switch_to_single_aggregate(page, "Sim A (1)")
    assert "timeout" in exc.value.reason


def test_successful_switch_does_not_false_fail_on_first_mismatch():
    """Immediate post-check mismatch is tolerated until polling confirms."""
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    ui = _DelayedAggUI({"Old": True, "ЧБР": False}, delay_reads=3)
    aw = _patch_delayed_switch(ui, page)

    with _switch_patches(ui, aw):
        switch_to_single_aggregate(page, "ЧБР")  # must not raise

    assert ui.state["ЧБР"] is True


def test_nested_fields_appear_with_delay_then_phone_filled():
    page = MagicMock()
    intent = _intent(phone="998927534931")
    calls = {"fields": 0, "fill": 0}

    def wait_fields(p, name):
        calls["fields"] += 1
        if calls["fields"] == 1:
            # first internal wait path via apply uses wait_for_aggregate_fields_visible once
            return

    def fill(p, intent_arg):
        calls["fill"] += 1
        assert calls["fields"] >= 1  # fields confirmed before fill

    with (
        patch(
            "automation.set_aggregate_engine.find_aggregate_state",
            return_value=True,
        ),
        patch(
            "automation.set_aggregate_engine.target_is_sole_active",
            return_value=False,
        ),
        patch("automation.set_aggregate_engine.switch_to_single_aggregate"),
        patch(
            "automation.set_aggregate_engine.read_top_level_phone",
            return_value="79001112233",
        ),
        patch(
            "automation.set_aggregate_engine.active_aggregate_names",
            return_value=["ЧБР"],
        ),
        patch(
            "automation.set_aggregate_engine.fill_set_aggregate_nested_fields",
            side_effect=fill,
        ),
        patch(
            "automation.set_aggregate_engine.wait_for_set_aggregate_fields_visible",
            side_effect=wait_fields,
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        err, top = apply_set_aggregate_on_open_form(page, intent)

    assert err is None
    assert top == "79001112233"
    assert calls["fill"] == 1
    assert calls["fields"] == 1


def test_phone_fill_only_after_switch_confirmed_order():
    page = MagicMock()
    intent = _intent(phone="998927534931")
    order: list[str] = []

    with (
        patch(
            "automation.set_aggregate_engine.find_aggregate_state",
            return_value=True,
        ),
        patch(
            "automation.set_aggregate_engine.target_is_sole_active",
            return_value=False,
        ),
        patch(
            "automation.set_aggregate_engine.switch_to_single_aggregate",
            side_effect=lambda *a, **k: order.append("switch"),
        ),
        patch(
            "automation.set_aggregate_engine.read_top_level_phone",
            return_value="79001112233",
        ),
        patch(
            "automation.set_aggregate_engine.active_aggregate_names",
            return_value=[],
        ),
        patch(
            "automation.set_aggregate_engine.fill_set_aggregate_nested_fields",
            side_effect=lambda *a, **k: order.append("fill"),
        ),
        patch(
            "automation.set_aggregate_engine.wait_for_set_aggregate_fields_visible",
            side_effect=lambda *a, **k: order.append("fields"),
        ),
    ):
        err, _ = apply_set_aggregate_on_open_form(page, intent)

    assert err is None
    assert order == ["switch", "fields", "fill"]


def test_top_level_phone_not_written_during_fill():
    page = MagicMock()
    intent = _intent(phone="998927534931")
    top_fill_calls = []

    with (
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            side_effect=[False, True],  # no nested card, nested phone yes
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw = aw_mod.return_value
        aw.MODAL_BODY = "#modal"
        modal = MagicMock()
        page.locator.return_value = modal

        def fill_if_present(m, labels, value, required=False):
            if labels == ("Телефон",):
                top_fill_calls.append(("nested", value))

        aw.fill_aggregate_field_if_present.side_effect = fill_if_present
        fill_set_aggregate_nested_fields(page, intent)

    assert top_fill_calls == [("nested", "998927534931")]
    assert not hasattr(aw, "_fill_text_by_label") or not aw._fill_text_by_label.called


def test_stop_before_save_after_fill_not_before():
    page = MagicMock()
    intent = _intent(phone="998927534931")
    cfg = _cfg(stop_before_save=True, stop_before_save_pause_ms=0)
    order: list[str] = []

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete",
            side_effect=lambda *a, **k: order.append("open") or 0,
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            side_effect=lambda *a, **k: (order.append("fill_done") or (None, "7900")),
        ),
        patch(
            "automation.engine._pause_stop_before_save",
            side_effect=lambda *a, **k: order.append("stop"),
        ),
        patch(
            "automation.add_wallet_engine.save_add_wallet_modal",
            side_effect=lambda *a, **k: order.append("save"),
        ) as save,
    ):
        result = ensure_set_aggregate(page, intent, cfg)

    assert result == RESULT_STOP_BEFORE_SAVE
    assert order == ["open", "fill_done", "stop"]
    save.assert_not_called()


def test_already_sole_active_skips_switch_but_fills_card():
    page = MagicMock()
    intent = _intent(phone=None, account=None)

    with (
        patch(
            "automation.set_aggregate_engine.find_aggregate_state",
            return_value=True,
        ),
        patch(
            "automation.set_aggregate_engine.target_is_sole_active",
            return_value=True,
        ),
        patch(
            "automation.set_aggregate_engine.switch_to_single_aggregate"
        ) as switch,
        patch(
            "automation.set_aggregate_engine.read_top_level_phone",
            return_value="79001112233",
        ),
        patch(
            "automation.set_aggregate_engine.active_aggregate_names",
            return_value=["ЧБР"],
        ),
        patch(
            "automation.set_aggregate_engine.fill_set_aggregate_nested_fields"
        ) as fill,
        patch(
            "automation.set_aggregate_engine.wait_for_set_aggregate_fields_visible"
        ),
    ):
        err, top = apply_set_aggregate_on_open_form(page, intent)

    assert err is None
    assert top == "79001112233"
    switch.assert_not_called()
    fill.assert_called_once()


def test_nested_card_filled_only_when_field_present():
    page = MagicMock()
    intent = _intent(phone=None)

    with (
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            return_value=True,
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw = aw_mod.return_value
        aw.MODAL_BODY = "#modal"
        page.locator.return_value = MagicMock(name="modal")
        fill_set_aggregate_nested_fields(page, intent)
        aw.fill_aggregate_field_if_present.assert_any_call(
            page.locator.return_value, ("Карта",), intent.card, required=False
        )


def test_aggregate_without_nested_card_skips_card_fill():
    page = MagicMock()
    intent = _intent(aggregate="Sim A (1)", phone="998927534931")

    with (
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            side_effect=[False, True],  # card absent, phone present
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw = aw_mod.return_value
        aw.MODAL_BODY = "#modal"
        page.locator.return_value = MagicMock()
        fill_set_aggregate_nested_fields(page, intent)
        labels = [c.args[1] for c in aw.fill_aggregate_field_if_present.call_args_list]
        assert ("Карта",) not in labels
        assert ("Телефон",) in labels


def test_phone_fills_only_nested_aggregate_field():
    page = MagicMock()
    intent = _intent(phone="998901234567")

    with (
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            side_effect=[False, True],
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw = aw_mod.return_value
        aw.MODAL_BODY = "#modal"
        modal = MagicMock()
        page.locator.return_value = modal
        fill_set_aggregate_nested_fields(page, intent)

        phone_calls = [
            c
            for c in aw.fill_aggregate_field_if_present.call_args_list
            if c.args[1] == ("Телефон",)
        ]
        assert len(phone_calls) == 1
        assert phone_calls[0].args[2] == "998901234567"


def test_missing_phone_column_does_not_touch_phones():
    page = MagicMock()
    intent = _intent(phone=None)  # column absent

    with (
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            return_value=False,
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw = aw_mod.return_value
        aw.MODAL_BODY = "#modal"
        page.locator.return_value = MagicMock()
        fill_set_aggregate_nested_fields(page, intent)
        labels_touched = [c.args[1] for c in aw.fill_aggregate_field_if_present.call_args_list]
        assert ("Телефон",) not in labels_touched


def test_optional_fields_only_when_columns_present():
    page = MagicMock()
    intent = _intent(
        phone="1",
        account="acc",
        merchant_id_sbp="mid",
        account_number="4082",
    )
    with (
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            return_value=True,
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw = aw_mod.return_value
        aw.MODAL_BODY = "#modal"
        aw.ACCOUNT_NUMBER_LABELS = ("Номер счёта",)
        page.locator.return_value = MagicMock()
        fill_set_aggregate_nested_fields(page, intent)
        assert aw.fill_aggregate_field_if_present.call_count == 5


def test_absent_optional_fields_not_cleared():
    page = MagicMock()
    intent = _intent(
        phone=None, account=None, merchant_id_sbp=None, account_number=None
    )
    with (
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            return_value=True,
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw = aw_mod.return_value
        aw.MODAL_BODY = "#modal"
        page.locator.return_value = MagicMock()
        fill_set_aggregate_nested_fields(page, intent)
        # Only nested card when present
        assert aw.fill_aggregate_field_if_present.call_count == 1


def test_unknown_aggregate_no_save():
    page = MagicMock()
    intent = _intent(aggregate="НЕТ_ТАКОГО")
    cfg = _cfg()

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete", return_value=0
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            return_value=(RESULT_FAIL_AGGREGATE_NOT_FOUND, "7900"),
        ) as apply,
        patch("automation.engine._close_stale_modal"),
        patch("automation.add_wallet_engine.save_add_wallet_modal") as save,
    ):
        result = ensure_set_aggregate(page, intent, cfg)

    assert result == RESULT_FAIL_AGGREGATE_NOT_FOUND
    apply.assert_called_once()
    save.assert_not_called()


def test_switch_error_no_save():
    page = MagicMock()
    intent = _intent()
    cfg = _cfg()

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete", return_value=0
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            return_value=(RESULT_FAIL_AGGREGATE_SWITCH, "7900"),
        ),
        patch("automation.engine._close_stale_modal"),
        patch("automation.add_wallet_engine.save_add_wallet_modal") as save,
    ):
        result = ensure_set_aggregate(page, intent, cfg)

    assert result == RESULT_FAIL_AGGREGATE_SWITCH
    save.assert_not_called()


def test_stop_before_save_skips_save():
    page = MagicMock()
    intent = _intent()
    cfg = _cfg(stop_before_save=True, stop_before_save_pause_ms=0)

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete", return_value=0
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            return_value=(None, "7900"),
        ),
        patch("automation.engine._pause_stop_before_save"),
        patch("automation.add_wallet_engine.save_add_wallet_modal") as save,
    ):
        result = ensure_set_aggregate(page, intent, cfg)

    assert result == RESULT_STOP_BEFORE_SAVE
    save.assert_not_called()


def test_successful_flow_with_verify():
    page = MagicMock()
    intent = _intent(phone="998901234567")
    cfg = _cfg()

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete", return_value=0
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            return_value=(None, "79001112233"),
        ),
        patch(
            "automation.add_wallet_engine.save_add_wallet_modal",
            return_value=SaveWaitOutcome(status="closed"),
        ) as save,
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch(
            "automation.engine.find_strict_matching_row_index", return_value=0
        ),
        patch("automation.engine.open_matched_card_row"),
        patch(
            "automation.set_aggregate_engine.verify_set_aggregate",
            return_value=None,
        ) as verify,
        patch("automation.engine._close_stale_modal"),
    ):
        result = ensure_set_aggregate(page, intent, cfg)

    assert result == RESULT_OK_SET_AGGREGATE
    save.assert_called_once()
    verify.assert_called_once()
    assert verify.call_args.kwargs["expected_top_phone"] == "79001112233"


def test_fail_verify_on_mismatch():
    page = MagicMock()
    intent = _intent()
    cfg = _cfg()

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete", return_value=0
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            return_value=(None, "7900"),
        ),
        patch(
            "automation.add_wallet_engine.save_add_wallet_modal",
            return_value=SaveWaitOutcome(status="closed"),
        ),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch(
            "automation.engine.find_strict_matching_row_index", return_value=0
        ),
        patch("automation.engine.open_matched_card_row"),
        patch(
            "automation.set_aggregate_engine.verify_set_aggregate",
            return_value="nested card mismatch",
        ),
        patch("automation.engine._close_stale_modal"),
    ):
        result = ensure_set_aggregate(page, intent, cfg)

    assert result == RESULT_FAIL_VERIFY


def test_skip_not_found():
    page = MagicMock()
    intent = _intent()
    cfg = _cfg()

    with patch(
        "automation.engine.find_and_open_card_for_delete", return_value=None
    ):
        assert ensure_set_aggregate(page, intent, cfg) == RESULT_SKIP_NOT_FOUND


def test_validation_and_timeout_mapping():
    page = MagicMock()
    intent = _intent()
    cfg = _cfg()

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete", return_value=0
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            return_value=(None, "7900"),
        ),
        patch(
            "automation.add_wallet_engine.save_add_wallet_modal",
            return_value=SaveWaitOutcome(status="validation", detail="err"),
        ),
        patch("automation.engine._close_stale_modal"),
    ):
        assert ensure_set_aggregate(page, intent, cfg) == RESULT_FAIL_VALIDATION

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete", return_value=0
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            return_value=(None, "7900"),
        ),
        patch(
            "automation.add_wallet_engine.save_add_wallet_modal",
            return_value=SaveWaitOutcome(status="timeout"),
        ),
        patch("automation.engine._close_stale_modal"),
    ):
        assert ensure_set_aggregate(page, intent, cfg) == RESULT_FAIL_SAVE_TIMEOUT


def test_verify_checks_top_phone_unchanged_and_nested_only_provided():
    page = MagicMock()
    intent = _intent(phone="998901234567", account=None)

    with (
        patch(
            "automation.set_aggregate_engine.target_is_sole_active",
            return_value=True,
        ),
        patch(
            "automation.set_aggregate_engine.read_nested_field",
            side_effect=lambda page, labels: {
                ("Карта",): "9990080812345678",
                ("Телефон",): "998901234567",
            }.get(labels),
        ),
        patch(
            "automation.set_aggregate_engine.read_top_level_phone",
            return_value="79001112233",
        ),
        patch(
            "automation.set_aggregate_engine.detect_set_aggregate_expansion",
            return_value="Аккаунт",
        ),
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            return_value=True,
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw_mod.return_value.ACCOUNT_NUMBER_LABELS = ("Номер счёта",)
        assert (
            verify_set_aggregate(page, intent, expected_top_phone="79001112233")
            is None
        )


def test_verify_fails_when_top_phone_changed():
    page = MagicMock()
    intent = _intent(phone=None)

    with (
        patch(
            "automation.set_aggregate_engine.target_is_sole_active",
            return_value=True,
        ),
        patch(
            "automation.set_aggregate_engine.read_nested_field",
            return_value="9990080812345678",
        ),
        patch(
            "automation.set_aggregate_engine.read_top_level_phone",
            return_value="CHANGED",
        ),
        patch(
            "automation.set_aggregate_engine.detect_set_aggregate_expansion",
            return_value="Аккаунт",
        ),
        patch(
            "automation.set_aggregate_engine._nested_field_visible",
            return_value=True,
        ),
        patch("automation.set_aggregate_engine._aw") as aw_mod,
    ):
        aw_mod.return_value.ACCOUNT_NUMBER_LABELS = ("Номер счёта",)
        reason = verify_set_aggregate(
            page, intent, expected_top_phone="79001112233"
        )
        assert reason == "top-level phone changed"


def test_status_and_timing_mapping():
    assert _status_from_set_aggregate_result(RESULT_OK_SET_AGGREGATE) == RESULT_OK_SET_AGGREGATE
    assert _status_from_set_aggregate_result(RESULT_FAIL_VERIFY) == RESULT_FAIL_VERIFY
    assert timing_outcome_from_result(RESULT_OK_SET_AGGREGATE) == "ok"
    assert timing_outcome_from_result(RESULT_STOP_BEFORE_SAVE) == "skip"
    assert timing_outcome_from_result(RESULT_FAIL_AGGREGATE_FIELDS) == "fail"


def test_run_processes_rows_independently(tmp_path):
    path = tmp_path / "multi.xlsx"
    pd.DataFrame(
        {
            "card": ["1111111111111111", "2222222222222222"],
            "action": ["set_aggregate", "set_aggregate"],
            "value": ["ЧБР", "Тинькофф АПК"],
        }
    ).to_excel(path, index=False)

    mock_sync, mock_page = _playwright_mocks()
    results = {0: RESULT_OK_SET_AGGREGATE, 1: RESULT_SKIP_NOT_FOUND}
    calls = []

    def fake_ensure(page, intent, cfg):
        calls.append(intent.card)
        # map by order
        return results[len(calls) - 1]

    with (
        patch("automation.engine.sync_playwright", return_value=mock_sync),
        patch("automation.engine._ensure_logged_in"),
        patch("automation.engine.require_wallet_editor_antares_credentials"),
        patch("automation.engine.load_hold_pairs_snapshot") as hold,
        patch("automation.engine.ensure_set_aggregate", side_effect=fake_ensure),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.close_playwright_stack"),
    ):
        hold.return_value = MagicMock(available=True, pairs=set(), error=None)
        out, stats = __import__("automation.engine", fromlist=["run"]).run(
            str(path), _cfg(result_file_path=str(tmp_path / "out.xlsx"))
        )

    assert calls == ["1111111111111111", "2222222222222222"]
    assert stats.ok == 1
    assert stats.skip == 1
    result_df = pd.read_excel(out)
    assert list(result_df["status"]) == [
        RESULT_OK_SET_AGGREGATE,
        RESULT_SKIP_NOT_FOUND,
    ]


def _playwright_mocks():
    mock_p = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page
    mock_sync = MagicMock()
    mock_sync.__enter__.return_value = mock_p
    mock_sync.__exit__.return_value = False
    return mock_sync, mock_page


def test_apply_unknown_aggregate_returns_fail_not_found_without_switch():
    page = MagicMock()
    with (
        patch(
            "automation.set_aggregate_engine.find_aggregate_state",
            return_value=None,
        ),
        patch(
            "automation.set_aggregate_engine.read_top_level_phone",
            return_value="7900",
        ),
        patch(
            "automation.set_aggregate_engine.active_aggregate_names",
            return_value=[],
        ),
        patch(
            "automation.set_aggregate_engine.switch_to_single_aggregate"
        ) as switch,
    ):
        err, _ = apply_set_aggregate_on_open_form(page, _intent())
    assert err == RESULT_FAIL_AGGREGATE_NOT_FOUND
    switch.assert_not_called()


def _make_for_id_label(text: str, for_id: str, *, checkbox_checked: bool = False):
    label = MagicMock()
    label.inner_text.return_value = text
    label.get_attribute.side_effect = lambda name: for_id if name == "for" else None
    # not inside custom-checkbox fallback
    empty = MagicMock()
    empty.count.return_value = 0
    label.locator.return_value = empty
    return label


def test_aggregate_binding_uses_exact_for_id():
    from automation.set_aggregate_engine import aggregate_checkbox_for_label

    modal = MagicMock()
    label = _make_for_id_label("Sim A (1)", "__BVID__4282")
    cb = MagicMock()
    cb.count.return_value = 1
    cb.first = MagicMock()
    modal.locator.return_value = cb

    found = aggregate_checkbox_for_label(modal, label)
    assert found is cb.first
    modal.locator.assert_called_with('input[type="checkbox"][id="__BVID__4282"]')


def test_aggregate_label_matches_with_inner_span_text():
    from automation.set_aggregate_engine import (
        _normalize_aggregate_label_text,
        aggregate_label_matches,
    )

    # Playwright inner_text flattens <span>(1)</span>
    text = _normalize_aggregate_label_text("Sim A\n(1)")
    assert aggregate_label_matches(text, "Sim A (1)")
    assert aggregate_label_matches("Sim A (1)", "Sim A (1)")


def test_field_captions_near_checkboxes_are_not_aggregates():
    """«Бакай» / «Номер слота» without for→checkbox must not appear in aggregate list."""
    from automation.set_aggregate_engine import list_aggregate_checkbox_states

    page = MagicMock()
    modal = MagicMock()

    sim_label = _make_for_id_label("Sim A (1)", "agg-1")
    bakai = MagicMock()
    bakai.inner_text.return_value = "Бакай"
    bakai.get_attribute.return_value = None  # no for=
    empty = MagicMock()
    empty.count.return_value = 0
    bakai.locator.return_value = empty  # not in custom-checkbox

    slot = MagicMock()
    slot.inner_text.return_value = "Номер слота"
    slot.get_attribute.return_value = None
    slot.locator.return_value = empty

    labels = MagicMock()
    labels.count.return_value = 3
    labels.nth.side_effect = lambda i: [sim_label, bakai, slot][i]

    sim_cb = MagicMock()
    sim_cb.count.return_value = 1
    sim_cb.first = MagicMock()
    sim_cb.first.is_checked.return_value = True

    def modal_locator(sel):
        if sel == "label":
            return labels
        if 'id="agg-1"' in sel:
            return sim_cb
        miss = MagicMock()
        miss.count.return_value = 0
        return miss

    modal.locator.side_effect = modal_locator
    page.locator.return_value = modal

    with patch("automation.set_aggregate_engine._aw") as aw_mod:
        aw_mod.return_value.MODAL_BODY = "#modal"
        aw_mod.return_value._is_kyc_label_text.return_value = False
        states = list_aggregate_checkbox_states(page)

    assert states == [("Sim A (1)", True)]
    assert [n for n, c in states if c] == ["Sim A (1)"]


def test_preceding_following_neighbor_checkbox_not_used():
    from automation.set_aggregate_engine import aggregate_checkbox_for_label

    modal = MagicMock()
    # Field label with no for= and no custom-checkbox ancestor
    label = MagicMock()
    label.get_attribute.return_value = None
    empty = MagicMock()
    empty.count.return_value = 0
    label.locator.return_value = empty

    assert aggregate_checkbox_for_label(modal, label) is None
    # Must not query neighboring XPath
    for call in label.locator.call_args_list:
        sel = call.args[0] if call.args else ""
        assert "preceding::" not in sel
        assert "following::" not in sel


def test_active_list_only_sim_a_when_field_labels_present():
    from automation.set_aggregate_engine import active_aggregate_names

    with patch(
        "automation.set_aggregate_engine.list_aggregate_checkbox_states",
        return_value=[("Sim A (1)", True)],
    ):
        assert active_aggregate_names(MagicMock()) == ["Sim A (1)"]


def test_sim_a_without_card_applies_and_fills_phone_then_stop():
    page = MagicMock()
    intent = _intent(aggregate="Sim A (1)", phone="998927534931")
    cfg = _cfg(stop_before_save=True, stop_before_save_pause_ms=0)
    order: list[str] = []

    with (
        patch(
            "automation.engine.find_and_open_card_for_delete",
            side_effect=lambda *a, **k: order.append("open") or 0,
        ),
        patch(
            "automation.set_aggregate_engine.apply_set_aggregate_on_open_form",
            side_effect=lambda *a, **k: (
                order.append("fill_done") or (None, "79001112233")
            ),
        ),
        patch(
            "automation.engine._pause_stop_before_save",
            side_effect=lambda *a, **k: order.append("stop"),
        ),
        patch(
            "automation.add_wallet_engine.save_add_wallet_modal",
            side_effect=lambda *a, **k: order.append("save"),
        ) as save,
    ):
        result = ensure_set_aggregate(page, intent, cfg)

    assert result == RESULT_STOP_BEFORE_SAVE
    assert order == ["open", "fill_done", "stop"]
    save.assert_not_called()
