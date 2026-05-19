"""C3.5 identity registry persistence (PR-4: local I/O only, no production hooks)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.rules_v2.identity_drift import (
    IdentityManifest,
    IdentityRegistry,
    IdentityRow,
    _jsonable_axes,
)
from core.rules_v2.snapshot_fingerprint import workbook_sha256

log = logging.getLogger(__name__)

REGISTRY_SCHEMA_VERSION = "rules_identity_registry.v1"
DEFAULT_REGISTRY_FILENAME = "rules_identity_registry.v1.json"
_ENV_REGISTRY_PATH = "RULES_IDENTITY_REGISTRY_PATH"
_ENV_RULES_XLSX_PATH = "RULES_XLSX_PATH"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _workbook_stat_key(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def _rules_folder_from_env() -> Path | None:
    raw = (os.getenv(_ENV_RULES_XLSX_PATH) or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_file() and p.suffix.lower() == ".xlsx":
        return p.parent
    return p


def default_identity_registry_path() -> Path:
    """Default local path: ``{RULES_FOLDER}/state/rules_identity_registry.v1.json``."""

    folder = _rules_folder_from_env()
    if folder is not None:
        return (folder / "state" / DEFAULT_REGISTRY_FILENAME).resolve()
    return Path("/tmp/rules_cache") / DEFAULT_REGISTRY_FILENAME


def resolve_identity_registry_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    override = (os.getenv(_ENV_REGISTRY_PATH) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_identity_registry_path()


def identity_registry_dropbox_path() -> str:
    """Dropbox-style path string for ``{folder}/state/rules_identity_registry.v1.json``."""

    raw = (os.getenv(_ENV_RULES_XLSX_PATH) or "").strip()
    if not raw:
        return f"/state/{DEFAULT_REGISTRY_FILENAME}"
    p = raw if raw.lower().endswith(".xlsx") else raw.rstrip("/") + "/rules.xlsx"
    if p.lower().endswith(".xlsx"):
        folder = p[: -len("rules.xlsx")].rstrip("/")
    else:
        folder = p.rstrip("/")
    return f"{folder}/state/{DEFAULT_REGISTRY_FILENAME}"


def registry_to_canonical_dict(registry: IdentityRegistry) -> dict[str, Any]:
    rows_obj: dict[str, Any] = {}
    for row in sorted(registry.rows, key=lambda r: (r.sheet, r.id)):
        rows_obj[row.key] = {
            "sheet": row.sheet,
            "id": row.id,
            "immutable_axes": _jsonable_axes(row.immutable_axes),
        }
    body: dict[str, Any] = {
        "schema_version": registry.schema_version or REGISTRY_SCHEMA_VERSION,
        "rows": rows_obj,
    }
    if registry.workbook_path is not None:
        body["workbook_path"] = registry.workbook_path
    if registry.workbook_sha256 is not None:
        body["workbook_sha256"] = registry.workbook_sha256
    if registry.stat_key is not None:
        body["stat_key"] = [registry.stat_key[0], registry.stat_key[1]]
    if registry.meta_version is not None:
        body["meta_version"] = registry.meta_version
    if registry.published_at_utc is not None:
        body["published_at_utc"] = registry.published_at_utc
    return body


def canonical_registry_json(registry: IdentityRegistry) -> str:
    return json.dumps(
        registry_to_canonical_dict(registry),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_registry_from_manifest(
    manifest: IdentityManifest,
    *,
    workbook_path: str | Path | None = None,
    meta_version: str = "legacy",
    published_at_utc: str | None = None,
) -> IdentityRegistry:
    """Build registry document metadata + manifest rows (no file write)."""

    wb = Path(workbook_path or manifest.workbook_path).resolve()
    stat = _workbook_stat_key(wb)
    return IdentityRegistry(
        rows=manifest.rows,
        schema_version=REGISTRY_SCHEMA_VERSION,
        workbook_path=str(wb),
        workbook_sha256=workbook_sha256(wb),
        stat_key=stat,
        meta_version=meta_version,
        published_at_utc=published_at_utc or _utc_now_iso(),
    )


def _parse_rows(payload: dict[str, Any]) -> tuple[IdentityRow, ...]:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, dict):
        return ()
    out: list[IdentityRow] = []
    for _key, item in raw_rows.items():
        if not isinstance(item, dict):
            continue
        sheet = item.get("sheet")
        row_id = item.get("id")
        axes = item.get("immutable_axes")
        if not isinstance(sheet, str) or not isinstance(row_id, str) or not isinstance(axes, dict):
            continue
        out.append(IdentityRow(sheet=sheet, id=row_id, immutable_axes=dict(axes)))
    out.sort(key=lambda r: (r.sheet, r.id))
    return tuple(out)


def load_identity_registry(path: str | Path | None = None) -> IdentityRegistry | None:
    """Load registry from disk; missing or corrupt file → ``None`` (no raise)."""

    target = resolve_identity_registry_path(path)
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("identity registry: failed to load %s: %s", target, exc)
        return None
    if not isinstance(payload, dict):
        log.warning("identity registry: root must be object: %s", target)
        return None
    schema = payload.get("schema_version")
    if schema != REGISTRY_SCHEMA_VERSION:
        log.warning(
            "identity registry: unsupported schema %r in %s (expected %s)",
            schema,
            target,
            REGISTRY_SCHEMA_VERSION,
        )
        return None
    stat_raw = payload.get("stat_key")
    stat_key: tuple[float, int] | None = None
    if isinstance(stat_raw, list) and len(stat_raw) == 2:
        try:
            stat_key = (float(stat_raw[0]), int(stat_raw[1]))
        except (TypeError, ValueError):
            stat_key = None
    return IdentityRegistry(
        rows=_parse_rows(payload),
        schema_version=str(schema),
        workbook_path=payload.get("workbook_path") if isinstance(payload.get("workbook_path"), str) else None,
        workbook_sha256=(
            payload.get("workbook_sha256") if isinstance(payload.get("workbook_sha256"), str) else None
        ),
        stat_key=stat_key,
        meta_version=payload.get("meta_version") if isinstance(payload.get("meta_version"), str) else None,
        published_at_utc=(
            payload.get("published_at_utc") if isinstance(payload.get("published_at_utc"), str) else None
        ),
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def save_identity_registry(registry: IdentityRegistry, path: str | Path | None = None) -> None:
    """Atomically write registry JSON (tmp + fsync + ``os.replace``)."""

    target = resolve_identity_registry_path(path)
    to_save = IdentityRegistry(
        rows=registry.rows,
        schema_version=registry.schema_version or REGISTRY_SCHEMA_VERSION,
        workbook_path=registry.workbook_path,
        workbook_sha256=registry.workbook_sha256,
        stat_key=registry.stat_key,
        meta_version=registry.meta_version,
        published_at_utc=registry.published_at_utc or _utc_now_iso(),
    )
    _atomic_write_text(target, canonical_registry_json(to_save))


__all__ = [
    "REGISTRY_SCHEMA_VERSION",
    "build_registry_from_manifest",
    "canonical_registry_json",
    "default_identity_registry_path",
    "identity_registry_dropbox_path",
    "load_identity_registry",
    "resolve_identity_registry_path",
    "save_identity_registry",
]
