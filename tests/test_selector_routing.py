"""Routing tests for analyzers/selector.py (explicit code constants, no YAML)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from analyzers import conversion as conversion_module
from analyzers import payout as payout_module
from analyzers import selector
from main import process_file

REPO_ROOT = Path(__file__).resolve().parents[1]

# DORMANT modules outside active prod chain — excluded from runtime-ref guard.
_DORMANT_RUNTIME_EXCLUDE = frozenset(
    {
        "analyzers/transactions.py",
    }
)


class TestSelectorConversionRouting:
    def test_selector_routes_conversion_without_yaml(self):
        run_fn, config, requires_card = selector.get_analyzer("conversion_12_00.xlsx")

        assert run_fn is conversion_module.run
        assert config == {}
        assert requires_card is True

    def test_selector_routes_conversion_requires_card(self):
        _, _, requires_card = selector.get_analyzer("conversion_test.xlsx")
        assert requires_card is True

    def test_selector_conversion_module_unchanged(self):
        run_fn, _, _ = selector.get_analyzer("report_conversion_12_00.xlsx")
        assert run_fn.__module__ == "analyzers.conversion"


class TestSelectorPayoutRouting:
    def test_selector_payout_unchanged(self):
        run_fn, config, requires_card = selector.get_analyzer("payout_12_00.xlsx")

        assert run_fn is payout_module.run
        assert config.get("file_pattern") == selector.PAYOUT_FILE_PATTERN
        assert requires_card is True


class TestProcessFileConversionContract:
    def test_process_file_conversion_contract_unchanged(self, tmp_path, monkeypatch):
        conv_name = "conversion_routing_test.xlsx"
        card_name = "card_routing_test.xlsx"
        conv_local = tmp_path / conv_name
        card_local = tmp_path / card_name
        conv_local.write_bytes(b"stub")
        card_local.write_bytes(b"stub")

        captured: dict = {}

        def fake_pipeline(conv_filename, card_filename=None, *, card_local_path=None, **kwargs):
            captured["conv_filename"] = conv_filename
            captured["card_filename"] = card_filename
            captured["card_local_path"] = card_local_path
            return True

        def tracking_get_analyzer(filename):
            run_fn, config, requires_card = selector.get_analyzer(filename)
            assert run_fn is conversion_module.run
            assert requires_card is True
            return run_fn, config, requires_card

        monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
        monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")
        monkeypatch.setattr("main.LOCAL_TMP_PATH", str(tmp_path))

        with patch("main.get_analyzer", side_effect=tracking_get_analyzer), patch(
            "integrations.conversion_pipeline.run_conversion_pipeline",
            side_effect=fake_pipeline,
        ):
            ok = process_file(conv_name, aux_filename=card_name)

        assert ok is True
        assert captured["conv_filename"] == conv_name
        assert captured["card_filename"] == card_name
        assert captured["card_local_path"] is None


def test_no_runtime_analysis_map_yaml_references():
    """Active runtime code must not reference config/analysis_map.yaml."""

    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("tests/"):
            continue
        if rel in _DORMANT_RUNTIME_EXCLUDE:
            continue
        text = path.read_text(encoding="utf-8")
        if "analysis_map.yaml" in text:
            offenders.append(rel)

    assert offenders == [], f"unexpected runtime references: {offenders}"


def test_analysis_map_yaml_removed_from_config():
    assert not (REPO_ROOT / "config" / "analysis_map.yaml").exists()
