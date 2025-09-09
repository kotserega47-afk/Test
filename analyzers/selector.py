# analyzers/selector.py
import os
import yaml

# Определяем путь до config/analysis_map.yaml относительно корня проекта
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "analysis_map.yaml")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    ANALYSIS_MAP = yaml.safe_load(f)


def get_analyzer(filename: str):
    """Определяет анализатор по имени файла"""
    for name, config in ANALYSIS_MAP.items():
        pattern = config.get("file_pattern")
        if pattern and pattern in filename.lower():
            module_name = f"analyzers.{name}"
            analyzer_module = __import__(module_name, fromlist=["run"])
            requires_card = pattern == "conversion"  # для conversion нужен card-файл
            return analyzer_module.run, config, requires_card
    return None, None, False
