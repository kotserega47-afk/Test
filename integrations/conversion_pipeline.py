"""Conversion file-lifecycle orchestrator with observability hooks."""

from __future__ import annotations

import os
import time
from typing import Any

from analyzers import conversion as conversion_module
from core.datetime_utils import now_msk
from core.event_log import append_event
from core.state_store import state_get, state_update
from integrations.conversion_fingerprint import ConversionFingerprint, build_conversion_fingerprint
from integrations.dropbox_watcher import download_file, move_file
from main import CONVERSION_COLUMNS, LOCAL_TMP_PATH, _safe_send
from utils.logger import logger

_CONVERSION_STATE_KEY = "conversion"
_EVENT_JOB_TYPE = "conversion"


def _fingerprint_enabled() -> bool:
    return os.getenv("CONVERSION_FINGERPRINT_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


def _runtime_sec(started_ts: float) -> float:
    return round(time.time() - started_ts, 3)


def _emit_event(event_type: str, payload: dict[str, Any]) -> None:
    append_event(type=event_type, job_type=_EVENT_JOB_TYPE, payload=payload)


def _safe_state_update(patch: dict[str, Any]) -> None:
    try:
        state_update(_CONVERSION_STATE_KEY, patch)
    except Exception as e:
        logger.warning(f"conversion observability: state_update failed: {e}")


def _base_payload(
    conv_filename: str,
    card_filename: str | None,
    source: str,
    started_ts: float,
) -> dict[str, Any]:
    return {
        "conv_filename": conv_filename,
        "card_filename": card_filename,
        "source": source,
        "runtime_sec": _runtime_sec(started_ts),
    }


def _prefix(value: str, length: int = 10) -> str:
    return value[:length] if value else ""


def _record_passive_fingerprint(
    fingerprint: ConversionFingerprint,
    *,
    matched_previous: bool,
) -> None:
    logger.info(
        "conversion fingerprint: combined=%s matched_previous=%s",
        _prefix(fingerprint.combined),
        matched_previous,
    )
    _safe_state_update(
        {
            "last_fingerprint_match": matched_previous,
            "last_fingerprint_checked_ts": int(time.time()),
        }
    )
    _emit_event(
        "conversion_fingerprint_computed",
        {
            "fingerprint_prefix": _prefix(fingerprint.combined),
            "matched_previous": matched_previous,
            "conv_hash_prefix": _prefix(fingerprint.conv_hash),
            "card_hash_prefix": _prefix(fingerprint.card_hash),
            "rules_hash_prefix": _prefix(fingerprint.rules_hash),
            "special_cards_hash_prefix": _prefix(fingerprint.special_cards_hash),
        },
    )


def run_conversion_pipeline(
    conv_filename: str,
    card_filename: str | None = None,
    *,
    conv_local_path: str | None = None,
    card_local_path: str | None = None,
    local_tmp_path: str | None = None,
    dropbox_input_path: str | None = None,
    dropbox_processed_path: str | None = None,
    col_mapping: dict | None = None,
    generate_excel: bool = True,
    send_telegram: bool = True,
    rules_force_sync: bool = False,
    move_processed: bool = True,
    source: str = "unknown",
) -> bool:
    """
    Mirror conversion branch of main.process_file() without selector/payout logic.

    Returns True on success, False on download/analyzer/move failure.
    """
    started_ts = time.time()
    tmp_root = local_tmp_path or LOCAL_TMP_PATH
    input_root = dropbox_input_path if dropbox_input_path is not None else os.getenv("DROPBOX_INPUT_PATH")
    processed_root = (
        dropbox_processed_path if dropbox_processed_path is not None else os.getenv("DROPBOX_PROCESSED_PATH")
    )
    mapping = col_mapping or CONVERSION_COLUMNS
    current_fingerprint: ConversionFingerprint | None = None
    fingerprint_matched_previous: bool | None = None
    previous_fingerprint_baseline: str | None = None

    logger.info(f"=== Conversion pipeline {conv_filename} ===")

    _emit_event(
        "conversion_started",
        {
            "conv_filename": conv_filename,
            "card_filename": card_filename,
            "source": source,
        },
    )
    _safe_state_update(
        {
            "last_run_ts": int(started_ts),
            "last_status": "running",
            "last_conv_filename": conv_filename,
            "last_card_filename": card_filename,
        }
    )

    def _record_fp_observation(outcome: str) -> None:
        if current_fingerprint is None or fingerprint_matched_previous is None:
            return
        try:
            from observability.conversion_fp_observation import record_conversion_fp_observation

            record_conversion_fp_observation(
                fingerprint=current_fingerprint,
                previous_fingerprint=previous_fingerprint_baseline,
                matched_previous=fingerprint_matched_previous,
                source=source,
                outcome=outcome,
                runtime_sec=_runtime_sec(started_ts),
            )
        except Exception as e:
            logger.warning(f"conversion fp observation: hook failed (best-effort): {e}")

    def _finish_skipped(reason: str) -> bool:
        payload = _base_payload(conv_filename, card_filename, source, started_ts)
        payload["reason"] = reason
        _emit_event("conversion_skipped", payload)
        _safe_state_update(
            {
                "last_status": "skipped",
                "last_runtime_sec": payload["runtime_sec"],
                "last_error": reason,
                "last_conv_filename": conv_filename,
                "last_card_filename": card_filename,
            }
        )
        _record_fp_observation("skipped")
        return False

    def _finish_failed(stage: str, err: str) -> bool:
        payload = _base_payload(conv_filename, card_filename, source, started_ts)
        payload["stage"] = stage
        payload["err"] = err
        _emit_event("conversion_failed", payload)
        _safe_state_update(
            {
                "last_status": "failed",
                "last_failure_ts": int(time.time()),
                "last_runtime_sec": payload["runtime_sec"],
                "last_error": err,
                "last_conv_filename": conv_filename,
                "last_card_filename": card_filename,
            }
        )
        _record_fp_observation("failed")
        return False

    def _finish_success(
        *,
        summary: dict | None,
        report_path: str | None,
        processed_name: str | None,
    ) -> bool:
        payload = _base_payload(conv_filename, card_filename, source, started_ts)
        payload["processed_name"] = processed_name
        payload["report_path"] = report_path
        payload["summary"] = summary or {}
        _emit_event("conversion_success", payload)

        success_patch: dict[str, Any] = {
            "last_status": "success",
            "last_success_ts": int(time.time()),
            "last_sent_ts": int(time.time()),
            "last_runtime_sec": payload["runtime_sec"],
            "last_error": None,
            "last_conv_filename": conv_filename,
            "last_card_filename": card_filename,
            "last_report_path": report_path,
        }
        if current_fingerprint is not None:
            success_patch["last_fingerprint"] = current_fingerprint.combined
            if fingerprint_matched_previous is not None:
                success_patch["last_fingerprint_match"] = fingerprint_matched_previous
        _safe_state_update(success_patch)
        _record_fp_observation("success")
        return True

    conv_local = conv_local_path or os.path.join(tmp_root, conv_filename)
    conv_dropbox = f"{input_root}/{conv_filename}"

    aux_local = card_local_path
    if card_filename and aux_local is None:
        aux_local = os.path.join(tmp_root, card_filename)

    if card_filename and aux_local is not None and not os.path.exists(aux_local):
        aux_dropbox = f"{input_root}/{card_filename}"
        if download_file(aux_dropbox, aux_local):
            logger.info(f"🧩 Вспомогательный файл скачан: {card_filename}")
            if move_processed:
                try:
                    aux_processed_path = (
                        f"{processed_root}/"
                        f"{card_filename[:-5]}_{now_msk().strftime('(%d.%m.%Y)')}.xlsx"
                    )
                    move_file(aux_dropbox, aux_processed_path)
                    logger.info(f"✅ Вспомогательный файл {card_filename} перемещён в /processed.")
                except Exception as e:
                    logger.warning(
                        f"⚠️ Не удалось переместить вспомогательный файл {card_filename}: {e}"
                    )
        else:
            logger.warning(f"⚠️ Не удалось скачать вспомогательный файл {card_filename}")
            aux_local = None

    current_date = now_msk().strftime("%d.%m.%Y")
    name, ext = os.path.splitext(conv_filename)
    filename_with_date = f"{name}_({current_date}){ext}"

    if not download_file(conv_dropbox, conv_local):
        msg = f"❌ Не удалось скачать файл {conv_filename} из Dropbox."
        logger.error(msg)
        _safe_send(msg)
        return _finish_failed("conv_download", msg)

    if not aux_local:
        msg = f"⚠️ Для {conv_filename} не найден вспомогательный файл (card/cd). Анализ пропущен."
        logger.warning(msg)
        _safe_send(msg)
        return _finish_skipped("card_missing")

    if _fingerprint_enabled():
        try:
            current_fingerprint = build_conversion_fingerprint(
                conv_local,
                aux_local,
                rules_force_sync=rules_force_sync,
                download_fn=download_file,
            )
            last_fingerprint = state_get(_CONVERSION_STATE_KEY, "last_fingerprint")
            previous_fingerprint_baseline = last_fingerprint
            fingerprint_matched_previous = (
                last_fingerprint == current_fingerprint.combined if last_fingerprint else False
            )
            _record_passive_fingerprint(
                current_fingerprint, matched_previous=fingerprint_matched_previous
            )
        except Exception as e:
            logger.warning(f"conversion fingerprint: passive compute failed: {e}")

    try:
        logger.info(f"🚀 Запуск анализа {conversion_module.__name__}.run()...")
        result = conversion_module.run(
            conv_local,
            [aux_local],
            mapping,
            generate_excel=generate_excel,
            send_telegram=send_telegram,
            rules_force_sync=rules_force_sync,
        )
        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        report_path = result.get("report_path") if isinstance(result, dict) else None
        logger.info(f"✅ Анализ завершён: {summary}")
    except Exception as e:
        msg = f"❌ Ошибка в анализаторе {conversion_module.__name__} для {conv_filename}: {e}"
        logger.exception(msg)
        _safe_send(msg)
        return _finish_failed("analyzer", str(e))

    if not move_processed:
        return _finish_success(
            summary=summary,
            report_path=report_path if isinstance(report_path, str) else None,
            processed_name=None,
        )

    try:
        move_file(conv_dropbox, f"{processed_root}/{filename_with_date}")
        logger.info(f"✅ Файл {conv_filename} перемещён в /processed.")
    except Exception as e:
        msg = f"⚠️ Ошибка при перемещении {conv_filename}: {e}"
        logger.error(msg)
        _safe_send(msg)
        return _finish_failed("conv_move", str(e))

    return _finish_success(
        summary=summary,
        report_path=report_path if isinstance(report_path, str) else None,
        processed_name=filename_with_date,
    )
