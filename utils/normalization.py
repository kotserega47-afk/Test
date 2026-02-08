# normalization.py

import re


def normalize_partner_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    normalized = name.lower().strip()
    normalized = normalized.replace("ё", "е")
    normalized = normalized.replace("амобайл", "а-мобайл")
    normalized = re.sub(r"\(\d+\)$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(", ")