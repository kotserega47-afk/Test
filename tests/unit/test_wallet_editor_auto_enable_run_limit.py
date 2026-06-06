from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from integrations.wallet_editor_auto_enable import build_phase_a_report, run_auto_enable
from integrations.wallet_editor_auto_enable_eligibility import (
    apply_run_limit,
    select_auto_enable_candidates,
    split_batches,
)
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    STATUS_K_VKLUCHENIYU,
)


def _settings(**overrides) -> AutoEnableSettings:
    base = dict(
        enabled=True,
        dry_run=True,
        approval_required=True,
        max_rows_per_batch=3,
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


def _row(i: int) -> dict[str, str]:
    return {
        "Дата отключения": f"{(i % 28) + 1:02d}.06.2026 10:00:00",
        "Дата включения": "06.06.2026",
        "Статус включения": STATUS_K_VKLUCHENIYU,
        "Включено": "",
        "Комментарий включения": "",
        "card": f"c{i:04d}",
        "partner": f"p{i:04d}",
        "action": ACTION_REMOVE_PARTNER,
        "status": "OK",
        "comment": "",
        "hold": "",
    }


def _registry_frames(n: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = pd.DataFrame([_row(i) for i in range(n)])
    return df, pd.DataFrame(), pd.DataFrame(), df


def test_phase_a_report_contains_run_limit_fields():
    settings = _settings(max_rows_per_run=3)
    df = _registry_frames(297)[0]
    eligibility = select_auto_enable_candidates(df, include_overdue=True)
    candidates_for_run = apply_run_limit(
        eligibility.selected,
        max_rows_per_run=settings.max_rows_per_run,
    )
    batches = split_batches(candidates_for_run, max_rows_per_batch=settings.max_rows_per_batch)

    report = build_phase_a_report(
        settings=settings,
        eligibility=eligibility,
        batches=batches,
        manual=True,
        selected_for_run=len(candidates_for_run),
    )

    assert "selected after dedup: 297" in report
    assert "max_rows_per_run: 3" in report
    assert "selected for this run: 3" in report
    assert "limited by max_rows_per_run: yes" in report
    assert "Run limited by max_rows_per_run: selected 3 of 297 candidates" in report
    assert "batch sizes: [3]" in report


def test_dry_run_respects_max_rows_per_run():
    settings = _settings(max_rows_per_run=3, max_rows_per_batch=3)
    frames = _registry_frames(297)
    with patch(
        "integrations.wallet_editor_auto_enable._send_to_route",
        return_value=True,
    ):
        result = run_auto_enable(
            settings=settings,
            manual=True,
            registry_frames=frames,
        )

    assert "selected for this run: 3" in result.report_text
    assert "batch sizes: [3]" in result.report_text
    assert "Run limited by max_rows_per_run" in result.report_text


def test_phase_b1_executor_receives_only_limited_candidates():
    settings = _settings(
        dry_run=False,
        approval_required=False,
        max_rows_per_run=3,
        max_rows_per_batch=3,
    )
    frames = _registry_frames(297)
    with patch(
        "integrations.wallet_editor_auto_enable._send_to_route",
        return_value=True,
    ):
        with patch(
            "integrations.wallet_editor_auto_enable.send_file_to_route",
            return_value=True,
        ):
            with patch(
                "integrations.wallet_editor_auto_enable.execute_enable_batch",
                return_value=[],
            ) as execute:
                with patch(
                    "integrations.wallet_editor_auto_enable.write_outcomes_report",
                ):
                    with patch("integrations.wallet_editor_auto_enable.os.remove"):
                        with patch(
                            "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
                        ) as append_registry:
                            run_auto_enable(
                                settings=settings,
                                manual=True,
                                registry_frames=frames,
                            )

    execute.assert_called_once()
    batch_arg = execute.call_args.args[0]
    assert len(batch_arg) == 3
    append_registry.assert_not_called()


def test_max_rows_per_run_zero_no_limit_in_report():
    settings = _settings(max_rows_per_run=0, max_rows_per_batch=200)
    frames = _registry_frames(10)
    with patch(
        "integrations.wallet_editor_auto_enable._send_to_route",
        return_value=True,
    ):
        result = run_auto_enable(
            settings=settings,
            manual=True,
            registry_frames=frames,
        )

    assert "selected after dedup: 10" in result.report_text
    assert "selected for this run: 10" in result.report_text
    assert "limited by max_rows_per_run: no" in result.report_text
    assert "Run limited by max_rows_per_run" not in result.report_text
