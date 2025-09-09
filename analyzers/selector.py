# analyzers/selector.py
import yaml
from analyzers import transactions
from analyzers import conversion

# Загружаем маппинг анализаторов
with open("config/analysis_map.yaml", "r", encoding="utf-8") as f:
    ANALYSIS_MAP = yaml.safe_load(f)

# Регистрируем функции для ключей из YAML
ANALYZERS = {
    "transactions": transactions.analyze,
    "conversion": conversion.run,
}

def get_analyzer(file_name: str):
    """
    Возвращает (функция_анализатора, конфиг) для файла по имени.
    Если анализатор не найден, возвращает (None, None).
    """
    file_name_lower = file_name.lower()
    for key, conf in ANALYSIS_MAP.items():
        pattern = conf.get("file_pattern", "").lower()
        if pattern and pattern in file_name_lower:
            return ANALYZERS.get(key), conf
    return None, None
