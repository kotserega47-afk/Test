import pandas as pd
import pytest

from automation.engine import (
    ALLOWED_ACTIONS,
    _prepare_df,
    _validate_set_direction_pre_playwright,
)


def test_set_direction_is_allowed_action():
    assert "set_direction" in ALLOWED_ACTIONS


def test_prepare_df_accepts_set_direction(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["set_direction"], "value": ["Вход"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "set_direction"
    assert df.iloc[0]["value"] == "Вход"


def test_set_direction_action_case_insensitive(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["SET_DIRECTION"], "value": ["Вход"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "set_direction"


def test_empty_set_direction_pre_playwright_fail():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["set_direction"],
            "value": [""],
            "status": [""],
            "comment": [""],
        }
    )

    fail_count = _validate_set_direction_pre_playwright(df)

    assert fail_count == 1
    assert df.iloc[0]["status"] == "FAIL"
    assert df.iloc[0]["comment"] == "пустое значение для set_direction"


def test_empty_set_direction_whitespace_only_pre_playwright_fail():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["set_direction"],
            "value": ["   "],
            "status": [""],
            "comment": [""],
        }
    )

    fail_count = _validate_set_direction_pre_playwright(df)

    assert fail_count == 1
    assert df.iloc[0]["status"] == "FAIL"


def test_prepare_df_still_accepts_set_status(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["set_status"],
            "value": ["Готов к работе"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "set_status"
    assert df.iloc[0]["value"] == "Готов к работе"


def test_add_group_is_allowed_action():
    assert "add_group" in ALLOWED_ACTIONS


def test_set_group_is_allowed_action():
    assert "set_group" in ALLOWED_ACTIONS


def test_prepare_df_accepts_add_group(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["add_group"], "value": ["Group A"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "add_group"


def test_prepare_df_accepts_set_group(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["set_group"], "value": ["Group A"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "set_group"


def test_add_group_action_normalized_lowercase(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["ADD_GROUP"], "value": ["Group A"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "add_group"


def test_set_group_action_normalized_lowercase(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["SET_GROUP"], "value": ["Group A"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "set_group"


def test_empty_add_group_pre_playwright_fail():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["add_group"],
            "value": [""],
            "status": [""],
            "comment": [""],
        }
    )

    fail_count = _validate_set_direction_pre_playwright(df)

    assert fail_count == 1
    assert df.iloc[0]["status"] == "FAIL"
    assert df.iloc[0]["comment"] == "пустое значение для add_group"


def test_empty_set_group_pre_playwright_fail():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["set_group"],
            "value": ["   "],
            "status": [""],
            "comment": [""],
        }
    )

    fail_count = _validate_set_direction_pre_playwright(df)

    assert fail_count == 1
    assert df.iloc[0]["status"] == "FAIL"
    assert df.iloc[0]["comment"] == "пустое значение для set_group"


def test_clear_groups_is_allowed_action():
    assert "clear_groups" in ALLOWED_ACTIONS


def test_prepare_df_accepts_clear_groups(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["clear_groups"], "value": [""]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "clear_groups"


def test_clear_groups_empty_value_valid_pre_playwright():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["clear_groups"],
            "value": [""],
            "status": [""],
            "comment": [""],
        }
    )

    fail_count = _validate_set_direction_pre_playwright(df)

    assert fail_count == 0
    assert df.iloc[0]["status"] == ""


def test_add_group_numeric_2_0_resolves_as_2(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["add_group"], "value": [2.0]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["value"] == "2"
    assert df.iloc[0]["status"] != "FAIL"


def test_set_group_numeric_2_0_resolves_as_2(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["set_group"], "value": [2.0]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["value"] == "2"


def test_group_value_2_5_pre_playwright_fail(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["add_group"], "value": [2.5]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["status"] == "FAIL"
    assert "недопустимое числовое значение" in df.iloc[0]["comment"]


def test_clear_groups_action_normalized_lowercase(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["CLEAR_GROUPS"], "value": [""]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "clear_groups"


def test_delete_is_allowed_action():
    assert "delete" in ALLOWED_ACTIONS


def test_prepare_df_accepts_delete_without_value(tmp_path):
    path = tmp_path / "delete.xlsx"
    pd.DataFrame(
        {"card": ["9860246700001620"], "action": ["delete"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "delete"
    assert df.iloc[0]["value"] == ""


def test_prepare_df_delete_normalized(tmp_path):
    path = tmp_path / "delete_norm.xlsx"
    pd.DataFrame(
        {"card": ["9860246700001620"], "action": [" DELETE "]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "delete"


def test_prepare_df_rejects_unknown_action(tmp_path):
    path = tmp_path / "wallet.xlsx"
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["unknown_action"], "value": ["x"]}
    ).to_excel(path, index=False)

    with pytest.raises(Exception, match="Недопустимые action"):
        _prepare_df(str(path))


def test_set_aggregate_is_allowed_action():
    assert "set_aggregate" in ALLOWED_ACTIONS


def test_prepare_df_accepts_set_aggregate(tmp_path):
    path = tmp_path / "set_aggregate.xlsx"
    pd.DataFrame(
        {
            "card": ["9990080812345678"],
            "action": ["Set_Aggregate"],
            "value": ["ЧБР"],
            "phone": ["998901234567"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "set_aggregate"
    assert df.iloc[0]["value"] == "ЧБР"
    assert df.iloc[0]["phone"] == "998901234567"


def test_empty_set_aggregate_pre_playwright_fail():
    df = pd.DataFrame(
        {
            "card": ["9990080812345678"],
            "action": ["set_aggregate"],
            "value": ["  "],
            "status": [""],
            "comment": [""],
        }
    )
    fail_count = _validate_set_direction_pre_playwright(df)
    assert fail_count == 1
    assert df.iloc[0]["status"] == "FAIL"
