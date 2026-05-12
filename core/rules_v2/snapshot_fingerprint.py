"""Deterministic fingerprint for ``RulesSnapshotV2`` (C4 logs, C5 baselines).

Uses only public dataclass fields. Does not import runtime ``RulesIndexes``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any

from core.rules_v2.models import RulesSnapshotV2


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return str(value)
    if isinstance(value, datetime):
        return {"_dt": "datetime", "iso": value.isoformat()}
    if isinstance(value, date):
        return {"_dt": "date", "iso": value.isoformat()}
    if isinstance(value, time):
        return {"_dt": "time", "iso": value.isoformat()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _dataclass_to_dict(value)
    raise TypeError(f"Unsupported snapshot value type: {type(value)!r}")


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(obj):
        out[f.name] = _jsonable(getattr(obj, f.name))
    return out


def rules_snapshot_fingerprint(snapshot: RulesSnapshotV2) -> str:
    """Return a hex SHA-256 of the canonical JSON encoding of ``snapshot``."""

    body = _jsonable(snapshot)
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def workbook_sha256(path: str | Path | bytes) -> str:
    """SHA-256 of raw workbook bytes (hex)."""

    if isinstance(path, bytes):
        return hashlib.sha256(path).hexdigest()
    p = Path(path)
    with p.open("rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()
