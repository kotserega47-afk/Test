# test_local.py

import os
from analyzers.selector import get_analyzer
from utils.report_builder import build_report
from utils.logger import logger

# Локальные папки
LOCAL_DATA = "data"
LOCAL_REPORTS = "reports"
os.makedirs(LOCAL_DATA, exist_ok=True)
os.makedirs(LOCAL_REPORTS, exist_ok=True)


def process_local_file(fname: str):
    """Тестовая обработка файла без Dropbox и Telegram"""
    local_file_path = os.path.join(LOCAL_DATA, fname)

    logger.info(f"Начинаем локальную обработку файла: {fname}")

    analyzer_func, config = get_analyzer(fname)
    if not analyzer_func or not config:
        print(f"❌ Не найден анализатор для файла {fname}")
        return

    try:
        columns = config.get("columns", {})

        if config.get("file_pattern") == "conversion":
            # ищем card-файл
            card_file = next((f for f in os.listdir(LOCAL_DATA) if "card" in f.lower()), None)
            if not card_file:
                print(f"❌ Для анализа {fname} не найден card-файл")
                return
            card_path = os.path.join(LOCAL_DATA, card_file)
            result = analyzer_func(local_file_path, card_path, columns)
        else:
            result = analyzer_func(local_file_path, columns)

        if not result:
            print(f"❌ Анализатор {fname} вернул пустой результат")
            return

        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")
        build_report(result, report_path)
        print(f"✅ Отчёт по {fname} готов: {report_path}")

    except Exception as e:
        logger.exception(f"Ошибка при обработке {fname}: {e}")
        print(f"❌ Ошибка при обработке {fname}: {e}")


if __name__ == "__main__":
    test_files = os.listdir(LOCAL_DATA)
    if not test_files:
        print("⚠️ Положите тестовые файлы в папку data/")
    else:
        for f in test_files:
            process_local_file(f)
