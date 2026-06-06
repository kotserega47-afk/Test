from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from integrations.wallet_editor_auto_enable import run_auto_enable
from integrations.wallet_editor_auto_enable_executor import (
    EnableOutcome,
    REGISTRY_OK,
    REGISTRY_SKIP,
)
from integrations.wallet_editor_registry import EnablePatchResult
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_registry_lifecycle import STATUS_K_VKLUCHENIYU


def _settings(**overrides) -> AutoEnableSettings:
    base = dict(
        enabled=True,
        dry_run=True,
        approval_required=True,
        max_rows_per_batch=200,
        max_rows_per_run=0,
        seconds_per_card_timeout=10,
        batch_timeout_buffer_seconds=300,
        allowed_statuses_for_enable=("готов к работе", "активный вход", "активный выход"),
        include_overdue=True,
        telegram_route_report="wallet_editor_auto_enable",
        telegram_route_alert="wallet_editor_auto_enable_alert",
    )
    base.update(overrides)
    return AutoEnableSettings(**base)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "Дата включения": "06.06.2026",
                "Статус включения": STATUS_K_VKLUCHENIYU,
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111",
                "partner": "Ostin",
                "action": "remove_partner",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        ]
    )


def _ok_outcome() -> EnableOutcome:
    return EnableOutcome(
        card="4111",
        partner="Ostin",
        disable_date="01.06.2026 10:00:00",
        registry_value=REGISTRY_OK,
        registry_comment="Партнёр уже был добавлен",
        status_before="Готов к работе",
        status_after="Готов к работе",
        partner_present_before=True,
        partner_present_after=True,
        mutated=False,
        saved=False,
        error_code="ALREADY_ADDED",
        source_row_index=0,
    )


@pytest.fixture
def registry_frames():
    df = _sample_df()
    return (df, pd.DataFrame(), pd.DataFrame(), df)


def test_dry_run_does_not_patch_registry(registry_frames):
    settings = _settings(dry_run=True)
    with patch("integrations.wallet_editor_auto_enable._send_to_route", return_value=True):
        with patch(
            "integrations.wallet_editor_auto_enable.patch_enable_results_in_dropbox_registry",
        ) as patch_registry:
            run_auto_enable(settings=settings, manual=True, registry_frames=registry_frames)

    patch_registry.assert_not_called()


def test_approval_required_does_not_patch_registry(registry_frames):
    settings = _settings(dry_run=False, approval_required=True)
    with patch("integrations.wallet_editor_auto_enable._send_to_route", return_value=True):
        with patch(
            "integrations.wallet_editor_auto_enable.patch_enable_results_in_dropbox_registry",
        ) as patch_registry:
            run_auto_enable(settings=settings, manual=True, registry_frames=registry_frames)

    patch_registry.assert_not_called()


def test_phase_b2_calls_registry_patch_after_executor(registry_frames):
    settings = _settings(dry_run=False, approval_required=False)
    with patch("integrations.wallet_editor_auto_enable._send_to_route", return_value=True):
        with patch("integrations.wallet_editor_auto_enable.send_file_to_route", return_value=True):
            with patch(
                "integrations.wallet_editor_auto_enable.execute_enable_batch",
                return_value=[_ok_outcome()],
            ):
                with patch(
                    "integrations.wallet_editor_auto_enable.write_outcomes_report",
                ):
                    with patch("integrations.wallet_editor_auto_enable.os.remove"):
                        with patch(
                            "integrations.wallet_editor_auto_enable.patch_enable_results_in_dropbox_registry",
                            return_value=EnablePatchResult(
                                success=True,
                                patched_count=1,
                                requested_count=1,
                            ),
                        ) as patch_registry:
                            result = run_auto_enable(
                                settings=settings,
                                manual=True,
                                registry_frames=registry_frames,
                            )

    patch_registry.assert_called_once()
    assert result.phase == "Phase B2 execution + registry patch"
    assert "registry_updated: True" in result.report_text
    assert "patched rows: 1" in result.report_text


def test_no_outcomes_does_not_patch_registry(registry_frames):
    settings = _settings(dry_run=False, approval_required=False, max_rows_per_run=0)
    empty_df = _sample_df().iloc[0:0]
    frames = (empty_df, pd.DataFrame(), pd.DataFrame(), empty_df)
    with patch("integrations.wallet_editor_auto_enable._send_to_route", return_value=True):
        with patch(
            "integrations.wallet_editor_auto_enable.execute_enable_batch",
        ) as execute:
            with patch(
                "integrations.wallet_editor_auto_enable.patch_enable_results_in_dropbox_registry",
            ) as patch_registry:
                run_auto_enable(settings=settings, manual=True, registry_frames=frames)

    execute.assert_not_called()
    patch_registry.assert_not_called()


def test_patch_failure_sends_alert_and_report_shows_false(registry_frames):
    settings = _settings(dry_run=False, approval_required=False)
    with patch(
        "integrations.wallet_editor_auto_enable._send_to_route",
        return_value=True,
    ) as send_route:
        with patch("integrations.wallet_editor_auto_enable.send_file_to_route", return_value=True):
            with patch(
                "integrations.wallet_editor_auto_enable.execute_enable_batch",
                return_value=[_ok_outcome()],
            ):
                with patch(
                    "integrations.wallet_editor_auto_enable.write_outcomes_report",
                ):
                    with patch("integrations.wallet_editor_auto_enable.os.remove"):
                        with patch(
                            "integrations.wallet_editor_auto_enable.patch_enable_results_in_dropbox_registry",
                            return_value=EnablePatchResult(
                                success=False,
                                patched_count=0,
                                requested_count=1,
                                error_reason="rev conflict",
                            ),
                        ):
                            result = run_auto_enable(
                                settings=settings,
                                manual=True,
                                registry_frames=registry_frames,
                            )

    assert "registry_updated: False" in result.report_text
    assert "patch failed reason: rev conflict" in result.report_text
    alert_calls = [
        call
        for call in send_route.call_args_list
        if call.args[0] == settings.telegram_route_alert
    ]
    assert len(alert_calls) == 1
    assert "Registry Patch Failed" in alert_calls[0].args[1]
