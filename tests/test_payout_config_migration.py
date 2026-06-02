"""Tests for payout_config.yaml → Rules V2 migration (CONFIG-MIGRATION-PHASE-1)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

from analyzers.payout_config_loader import (
    load_rules_payout_config,
    load_yaml_payout_config,
    payout_config_diff,
    resolve_payout_config,
    run_payout_config_shadow_compare,
)
from core.rules_v2.contract_schema import SHEET_SCHEMAS
from core.rules_v2.payout_rules_accessor import (
    PayoutRulesAccessor,
    PayoutRulesData,
    normalize_payout_config_for_compare,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = REPO_ROOT / "config" / "payout_config.yaml"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "payout"

# Rows mirroring config/payout_config.yaml (golden equivalence).
_GOLDEN_INFO_ROWS = [
    ("PIR-00001", 1, "ORA-20001: Sbp Members НЕ найден в базе", 5, "prod yaml"),
    (
        "PIR-00002",
        1,
        "Ошибка проверки Unistream_2: Unistream API Error: 404 Client Error",
        5,
        "prod yaml",
    ),
    (
        "PIR-00003",
        1,
        "Ошибка проверки Unistream_1: Unistream API Error: 406 Client Error",
        5,
        "prod yaml",
    ),
    (
        "PIR-00004",
        1,
        "ограничения законодательства - уровень идентификации денежных средств недостаточен",
        7,
        "prod yaml",
    ),
    ("PIR-00005", 1, "операция отклонена", 7, "prod yaml"),
    ("PIR-00006", 1, "операция отменена", 7, "prod yaml"),
    (
        "PIR-00007",
        1,
        "ошибка внешней системы.: (перевод не может быть осуществлен)",
        7,
        "prod yaml",
    ),
    ("PIR-00008", 1, "ошибка создания", 5, "prod yaml"),
    (
        "PIR-00009",
        1,
        "возникла техническая ошибка (-1). обратитесь в контакт-центр 777-77-77.",
        5,
        "prod yaml",
    ),
    ("PIR-00010", 1, "Нет услуги", 1, "prod yaml"),
]

_GOLDEN_IGNORE_ROWS = [
    ("PIP-00001", 1, "код возврата: i05043. отказ от банка получателя", "prod yaml"),
    ("PIP-00002", 1, "код возврата: i01091. нет ответа. таймаут", "prod yaml"),
    (
        "PIP-00003",
        1,
        "ошибка внешней системы.: (код возврата: i05043. отказ от банка получателя)",
        "prod yaml",
    ),
    ("PIP-00004", 1, "[b2c] d02 timeout", "prod yaml"),
    (
        "PIP-00005",
        1,
        "код возврата: i05034. подозрительная активность в отношении получателя",
        "prod yaml",
    ),
]


def _build_payout_rules_xlsx(path: Path, *, info_rows=None, ignore_rows=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    info_rows = info_rows if info_rows is not None else _GOLDEN_INFO_ROWS
    ignore_rows = ignore_rows if ignore_rows is not None else _GOLDEN_IGNORE_ROWS

    info_df = pd.DataFrame(
        info_rows,
        columns=["id", "enabled", "info_phrase", "threshold", "reason"],
    )
    ignore_df = pd.DataFrame(
        ignore_rows,
        columns=["id", "enabled", "info_phrase", "reason"],
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        info_df.to_excel(writer, sheet_name="payout_info_rules", index=False)
        ignore_df.to_excel(writer, sheet_name="payout_ignore_phrases", index=False)

    return path


@pytest.fixture
def golden_rules_xlsx(tmp_path: Path) -> Path:
    return _build_payout_rules_xlsx(tmp_path / "payout_rules_golden.xlsx")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_payout_info_rules_schema():
    schema = SHEET_SCHEMAS["payout_info_rules"]
    assert schema.required_columns == frozenset(
        {"id", "enabled", "info_phrase", "threshold", "reason"}
    )


def test_payout_ignore_phrases_schema():
    schema = SHEET_SCHEMAS["payout_ignore_phrases"]
    assert schema.required_columns == frozenset(
        {"id", "enabled", "info_phrase", "reason"}
    )


# ---------------------------------------------------------------------------
# Accessor
# ---------------------------------------------------------------------------


def test_payout_accessor_reads_rules(golden_rules_xlsx: Path):
    data = load_rules_payout_config(golden_rules_xlsx)
    assert data is not None
    assert len(data.payout_rules) == 10
    assert data.payout_rules["Нет услуги"]["threshold"] == 1
    assert data.payout_rules["операция отклонена"]["threshold"] == 7


def test_payout_accessor_ignore_phrases(golden_rules_xlsx: Path):
    data = load_rules_payout_config(golden_rules_xlsx)
    assert data is not None
    assert len(data.ignore_phrases) == 5
    assert "[b2c] d02 timeout" in data.ignore_phrases


def test_payout_accessor_skips_disabled_rows(tmp_path: Path):
    info_rows = list(_GOLDEN_INFO_ROWS)
    info_rows[0] = ("PIR-00001", 0, info_rows[0][2], info_rows[0][3], "disabled")
    xlsx = _build_payout_rules_xlsx(tmp_path / "disabled.xlsx", info_rows=info_rows)
    data = PayoutRulesAccessor.from_rules_path(xlsx)
    assert data is not None
    assert "ORA-20001: Sbp Members НЕ найден в базе" not in data.payout_rules
    assert len(data.payout_rules) == 9


# ---------------------------------------------------------------------------
# Shadow
# ---------------------------------------------------------------------------


def test_payout_shadow_no_diff(golden_rules_xlsx: Path, monkeypatch, caplog):
    yaml_data = load_yaml_payout_config(DEFAULT_YAML)
    rules_data = load_rules_payout_config(golden_rules_xlsx)
    assert payout_config_diff(yaml_data, rules_data) == {}

    import analyzers.payout_config_loader as loader_mod

    monkeypatch.setattr(loader_mod, "_resolve_rules_path", lambda: golden_rules_xlsx)

    caplog.set_level(logging.WARNING)
    run_payout_config_shadow_compare(
        logging.getLogger("test.payout.shadow"),
        yaml_path=DEFAULT_YAML,
    )
    assert not any("[config_shadow] payout mismatch" in r.message for r in caplog.records)


def test_payout_shadow_detects_diff(golden_rules_xlsx: Path, monkeypatch, caplog):
    yaml_data = load_yaml_payout_config(DEFAULT_YAML)
    rules_data = load_rules_payout_config(golden_rules_xlsx)
    assert rules_data is not None

    mutated = PayoutRulesData(
        payout_rules=dict(rules_data.payout_rules),
        ignore_phrases=list(rules_data.ignore_phrases),
    )
    mutated = PayoutRulesData(
        payout_rules={**mutated.payout_rules, "Нет услуги": {"threshold": 99}},
        ignore_phrases=mutated.ignore_phrases,
    )

    diff = payout_config_diff(yaml_data, mutated)
    assert "payout_rules" in diff

    import analyzers.payout_config_loader as loader_mod

    monkeypatch.setattr(loader_mod, "_resolve_rules_path", lambda: golden_rules_xlsx)
    monkeypatch.setattr(loader_mod, "load_rules_payout_config", lambda _p: mutated)

    caplog.set_level(logging.WARNING)
    logger = logging.getLogger("test.payout.shadow.diff")
    run_payout_config_shadow_compare(logger, yaml_path=DEFAULT_YAML)
    assert any("[config_shadow] payout mismatch" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


def test_payout_config_yaml_mode(golden_rules_xlsx: Path, monkeypatch, caplog):
    monkeypatch.delenv("PAYOUT_CONFIG_FROM_RULES_V2", raising=False)
    caplog.set_level(logging.INFO)

    norm_rules, norm_ignore, source = resolve_payout_config(
        logging.getLogger("test.payout.yaml"),
        yaml_path=DEFAULT_YAML,
        rules_path=golden_rules_xlsx,
    )

    assert source == "yaml"
    assert len(norm_rules) == 10
    assert len(norm_ignore) == 5
    assert not any("[payout_config] source=rules_v2" in r.message for r in caplog.records)


def test_payout_config_rules_mode(golden_rules_xlsx: Path, monkeypatch, caplog):
    monkeypatch.setenv("PAYOUT_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.payout.rules")

    norm_rules, norm_ignore, source = resolve_payout_config(
        logger,
        yaml_path=DEFAULT_YAML,
        rules_path=golden_rules_xlsx,
    )

    assert source == "rules_v2"
    assert len(norm_rules) == 10
    assert len(norm_ignore) == 5
    assert any("[payout_config] source=rules_v2" in r.message for r in caplog.records)


def test_payout_config_rules_fallback(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setenv("PAYOUT_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.payout.fallback")

    empty_xlsx = tmp_path / "empty_rules.xlsx"
    with pd.ExcelWriter(empty_xlsx, engine="openpyxl") as writer:
        pd.DataFrame(columns=["id", "enabled", "info_phrase", "threshold", "reason"]).to_excel(
            writer, sheet_name="payout_info_rules", index=False
        )
        pd.DataFrame(columns=["id", "enabled", "info_phrase", "reason"]).to_excel(
            writer, sheet_name="payout_ignore_phrases", index=False
        )

    norm_rules, norm_ignore, source = resolve_payout_config(
        logger,
        yaml_path=DEFAULT_YAML,
        rules_path=empty_xlsx,
    )

    assert source == "yaml"
    assert len(norm_rules) == 10
    assert any("[payout_config] source=yaml" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Golden: yaml ↔ rules normalized equivalence
# ---------------------------------------------------------------------------


def test_payout_golden_yaml_rules_equivalence(golden_rules_xlsx: Path):
    yaml_view = normalize_payout_config_for_compare(load_yaml_payout_config(DEFAULT_YAML))
    rules_view = normalize_payout_config_for_compare(load_rules_payout_config(golden_rules_xlsx))
    assert yaml_view == rules_view


def test_build_golden_fixture_on_disk():
    """Regenerate committed fixture if missing (CI / fresh checkout)."""

    fixture_path = FIXTURES_DIR / "payout_config_rules_golden.xlsx"
    if not fixture_path.exists():
        _build_payout_rules_xlsx(fixture_path)
    assert fixture_path.exists()
    yaml_view = normalize_payout_config_for_compare(load_yaml_payout_config(DEFAULT_YAML))
    rules_view = normalize_payout_config_for_compare(load_rules_payout_config(fixture_path))
    assert yaml_view == rules_view
