# analyzers/selector.py
import yaml
from analyzers import transactions  # добавляй другие анализаторы по мере необходимости

# Загружаем маппинг анализаторов
with open("config/analysis_map.yaml", "r", encoding="utf-8") as f:
    ANALYSIS_MAP = yaml.safe_load(f)


def get_analyzer(file_name: str):
    """
    Возвращает функцию анализа для файла по имени
    """
    file_name_lower = file_name.lower()
    for key, conf in ANALYSIS_MAP.items():
        pattern = conf.get("file_pattern", "").lower()
        if pattern in file_name_lower:
            if key == "transactions":
                return transactions.analyze
            # Здесь можно добавить другие анализаторы
            # elif key == "kyc":
            #     return kyc.analyze
    return None
