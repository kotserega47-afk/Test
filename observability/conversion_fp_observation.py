"""Conversion fingerprint diagnostic observation layer (Phase 1B — no dedup skip)."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from integrations.conversion_fingerprint import ConversionFingerprint
from utils.logger import logger

SCHEMA_VERSION = 1
RETENTION_DAYS = 14
_FILE_PREFIX = "conversion_fp_observation_"

_lock = threading.Lock()

_COMPONENT_KEYS = (
    ("conv", "conv_hash"),
    ("card", "card_hash"),
    ("rules", "rules_hash"),
    ("special_cards", "special_cards_hash"),
)


def observation_enabled() -> bool:
    return os.getenv("CONVERSION_FP_OBSERVATION_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


def _state_dir() -> Path:
    return Path(os.getenv("STATE_DIR", "/data/state"))


def _observability_dir() -> Path:
    return _state_dir() / "observability"


def _observation_path(day_ymd: str) -> Path:
    return _observability_dir() / f"{_FILE_PREFIX}{day_ymd}.jsonl"


def _day_ymd(ts: float | None = None) -> str:
    when = ts if ts is not None else time.time()
    return time.strftime("%Y-%m-%d", time.gmtime(when))


def compute_changed_components(
    current: ConversionFingerprint,
    baseline: ConversionFingerprint | None,
) -> list[str]:
    if baseline is None:
        return [name for name, _ in _COMPONENT_KEYS]

    changed: list[str] = []
    for name, attr in _COMPONENT_KEYS:
        if getattr(current, attr) != getattr(baseline, attr):
            changed.append(name)
    return changed


def _fingerprint_from_record(record: dict[str, Any]) -> ConversionFingerprint:
    return ConversionFingerprint(
        combined=record["fingerprint"],
        conv_hash=record["conv_hash"],
        card_hash=record["card_hash"],
        rules_hash=record["rules_hash"],
        special_cards_hash=record["special_cards_hash"],
    )


def _parse_day_from_filename(path: Path) -> datetime | None:
    stem = path.name
    if not stem.startswith(_FILE_PREFIX) or not stem.endswith(".jsonl"):
        return None
    day_text = stem[len(_FILE_PREFIX) : -len(".jsonl")]
    try:
        return datetime.strptime(day_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def find_last_successful_baseline(obs_dir: Path | None = None) -> ConversionFingerprint | None:
    """Return component hashes from the most recent successful observation record."""
    root = obs_dir if obs_dir is not None else _observability_dir()
    if not root.is_dir():
        return None

    files = sorted(
        (p for p in root.glob(f"{_FILE_PREFIX}*.jsonl") if _parse_day_from_filename(p)),
        key=lambda p: _parse_day_from_filename(p) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            logger.warning("conversion fp observation: read baseline failed (%s): %s", path, e)
            continue

        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("outcome") != "success":
                continue
            try:
                return _fingerprint_from_record(record)
            except (KeyError, TypeError):
                continue

    return None


def build_observation_record(
    *,
    fingerprint: ConversionFingerprint,
    previous_fingerprint: str | None,
    matched_previous: bool,
    source: str,
    outcome: str,
    runtime_sec: float,
    baseline: ConversionFingerprint | None,
    ts: float | None = None,
) -> dict[str, Any]:
    when = ts if ts is not None else time.time()
    changed_components = compute_changed_components(fingerprint, baseline)
    would_skip = matched_previous and outcome == "success"

    return {
        "schema_version": SCHEMA_VERSION,
        "ts": when,
        "source": source,
        "fingerprint": fingerprint.combined,
        "previous_fingerprint": previous_fingerprint,
        "matched_previous": matched_previous,
        "conv_hash": fingerprint.conv_hash,
        "card_hash": fingerprint.card_hash,
        "rules_hash": fingerprint.rules_hash,
        "special_cards_hash": fingerprint.special_cards_hash,
        "changed_components": changed_components,
        "outcome": outcome,
        "runtime_sec": round(runtime_sec, 3),
        "would_skip": would_skip,
    }


def append_observation_record(record: dict[str, Any], *, obs_dir: Path | None = None) -> None:
    root = obs_dir if obs_dir is not None else _observability_dir()
    root.mkdir(parents=True, exist_ok=True)
    day = _day_ymd(record.get("ts"))
    path = root / f"{_FILE_PREFIX}{day}.jsonl"
    line = json.dumps(record, ensure_ascii=False) + "\n"

    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def apply_retention(*, obs_dir: Path | None = None, retention_days: int = RETENTION_DAYS) -> None:
    root = obs_dir if obs_dir is not None else _observability_dir()
    if not root.is_dir():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for path in root.glob(f"{_FILE_PREFIX}*.jsonl"):
        day = _parse_day_from_filename(path)
        if day is None:
            continue
        if day < cutoff:
            try:
                path.unlink()
                logger.info("conversion fp observation: removed expired file %s", path.name)
            except OSError as e:
                logger.warning("conversion fp observation: retention delete failed (%s): %s", path, e)


def record_conversion_fp_observation(
    *,
    fingerprint: ConversionFingerprint,
    previous_fingerprint: str | None,
    matched_previous: bool,
    source: str,
    outcome: str,
    runtime_sec: float,
) -> None:
    if not observation_enabled():
        return

    try:
        baseline = find_last_successful_baseline()
        record = build_observation_record(
            fingerprint=fingerprint,
            previous_fingerprint=previous_fingerprint,
            matched_previous=matched_previous,
            source=source,
            outcome=outcome,
            runtime_sec=runtime_sec,
            baseline=baseline,
        )
        append_observation_record(record)
        apply_retention()
    except Exception as e:
        logger.warning("conversion fp observation: record failed (best-effort): %s", e)
