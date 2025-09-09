# analyzers/selector.py

import yaml
from analyzers import transactions
from analyzers import conversion
# from analyzers import kyc  # подключим позже

# Загружаем маппинг анализаторов
with open("config/analysis_map.yaml", "r", encoding="utf-8") as f:
    ANALYSIS_MAP = yaml.safe_load(f)

# Регистрируем функции для ключей из YAML
ANALYZERS = {
    "transactions": transactions.analyze,
    "conversion": conversion.run,
    # "kyc": kyc.analyze,
}


def get_analyzer(file_name: str):
    """
    Возвращает (функция_анализатора, конфиг, requires) для файла по имени.
    Если анализатор не найден, возвращает (None, None, None).
    """
    file_name_lower = file_name.lower()
    for key, conf in ANALYSIS_MAP.items():
        pattern = conf.get("file_pattern", "").lower()
        if pattern and pattern in file_name_lower:
            analyzer_func = ANALYZERS.get(key)
            requires = conf.get("requires", [])
            return analyzer_func, conf, requires
    return None, None, None
