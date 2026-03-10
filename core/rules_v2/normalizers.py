from __future__ import annotations

import json
import re
from typing import Any

from core.datetime_utils import parse_msk_datetime, parse_time_value
from core.rules_v2.constants import DEFAULT_TIMEZONE

_KEY_RE = re.compile(r"[^a-z0-9_]+")


def normalize_key(value: Any) -> str:
    return make_ascii_key(value)


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "да"}:
        return True
    if s in {"0", "false", "no", "n", "нет", ""}:
        return False

    raise ValueError(f"Cannot parse bool from {value!r}")


def parse_int(value: Any) -> int:
    s = str(value).strip()
    if not s:
        raise ValueError("Empty int value")
    return int(s)


def parse_float(value: Any) -> float:
    s = str(value).strip().replace(",", ".")
    if not s:
        raise ValueError("Empty float value")
    return float(s)


def normalize_scope_type(value: str) -> str:
    v = normalize_key(value)
    if v not in {"global", "group", "partner"}:
        raise ValueError(f"Invalid scope_type: {value!r}")
    return v


def normalize_schedule_type(value: str) -> str:
    v = normalize_key(value)
    if v not in {"interval", "cron"}:
        raise ValueError(f"Invalid schedule_type: {value!r}")
    return v


def normalize_timezone(value: Any) -> str:
    s = str(value).strip()
    return s or DEFAULT_TIMEZONE


def parse_typed_value(value: Any, value_type: str) -> Any:
    vt = normalize_key(value_type)

    if vt == "int":
        return parse_int(value)
    if vt == "float":
        return parse_float(value)
    if vt == "bool":
        return normalize_bool(value)
    if vt == "time":
        parsed = parse_time_value(value)
        if parsed is None:
            raise ValueError(f"Cannot parse time from {value!r}")
        return parsed
    if vt == "datetime":
        parsed = parse_msk_datetime(value)
        if parsed is None:
            raise ValueError(f"Cannot parse datetime from {value!r}")
        return parsed
    if vt == "str":
        return "" if value is None else str(value).strip()
    if vt == "json":
        return json.loads(value if isinstance(value, str) else str(value))

    raise ValueError(f"Unsupported value_type: {value_type!r}")

_PARTNER_CODE_RE = re.compile(r"\((\d+)\)\s*$")


def extract_partner_code(value: Any) -> str | None:
    if value is None:
        return None

    s = str(value).strip()
    m = _PARTNER_CODE_RE.search(s)
    if not m:
        return None

    return m.group(1)

_RU_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}

def transliterate_ru_to_ascii(text: str) -> str:
    out = []
    for ch in text:
        lower = ch.lower()
        if lower in _RU_TRANSLIT:
            out.append(_RU_TRANSLIT[lower])
        else:
            out.append(ch)
    return "".join(out)

def make_ascii_key(value: Any) -> str:
    if value is None:
        return ""

    s = str(value).strip().lower()
    s = s.replace("ё", "е")
    s = transliterate_ru_to_ascii(s)

    s = s.replace("-", "_").replace(" ", "_")

    s = _KEY_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")

    return s

def build_partner_key(name: Any) -> str:
    if name is None:
        return ""

    raw = str(name).strip()
    code = extract_partner_code(raw)

    base = raw
    if code:
        idx = base.rfind(f"({code})")
        if idx != -1:
            base = base[:idx]

    base = make_ascii_key(base)

    if code:
        if base:
            return f"{base}_{code}"
        return code

    return base

