# analyzers/selector.py
from analyzers import conversion as conversion_module
from analyzers import payout as payout_module

CONVERSION_FILE_PATTERN = "conversion"
PAYOUT_FILE_PATTERN = "payout"

# Backward-compatible config shape returned by get_analyzer (payout path only).
_PAYOUT_ROUTE_CONFIG = {
    "file_pattern": PAYOUT_FILE_PATTERN,
    "description": "Анализ выплатных файлов",
}


def _resolve_conversion(filename: str):
    """Explicit conversion routing."""
    if CONVERSION_FILE_PATTERN not in filename.lower():
        return None
    return conversion_module.run, {}, True


def _resolve_payout(filename: str):
    """Explicit payout routing."""
    if PAYOUT_FILE_PATTERN not in filename.lower():
        return None
    return payout_module.run, _PAYOUT_ROUTE_CONFIG, True


def get_analyzer(filename: str):
    """Определяет анализатор по имени файла."""
    conversion_match = _resolve_conversion(filename)
    if conversion_match is not None:
        return conversion_match

    payout_match = _resolve_payout(filename)
    if payout_match is not None:
        return payout_match

    return None, None, False
