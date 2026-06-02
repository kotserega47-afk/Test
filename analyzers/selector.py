# analyzers/selector.py
import os
import yaml

from analyzers import conversion as conversion_module

# Определяем путь до config/analysis_map.yaml относительно корня проекта
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "analysis_map.yaml")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    ANALYSIS_MAP = yaml.safe_load(f)

CONVERSION_FILE_PATTERN = "conversion"


def _resolve_conversion(filename: str):
    """Explicit conversion routing (does not use analysis_map.yaml)."""
    if CONVERSION_FILE_PATTERN not in filename.lower():
        return None
    return conversion_module.run, {}, True


def get_analyzer(filename: str):
    """Определяет анализатор по имени файла"""
    conversion_match = _resolve_conversion(filename)
    if conversion_match is not None:
        return conversion_match

    for name, config in ANALYSIS_MAP.items():
        if name == "conversion":
            continue
        pattern = config.get("file_pattern")
        if pattern and pattern in filename.lower():
            module_name = f"analyzers.{name}"
            analyzer_module = __import__(module_name, fromlist=["run"])
            requires_card = pattern in {"conversion", "payout"}
            return analyzer_module.run, config, requires_card
    return None, None, False
