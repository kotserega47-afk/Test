# app.py
import streamlit as st
import pandas as pd
import os

REPORTS_DIR = "reports"

st.set_page_config(page_title="Отчёты анализа", layout="wide")

st.title("📊 Центр анализа файлов")

# Проверяем папку с отчётами
if not os.path.exists(REPORTS_DIR):
    os.makedirs(REPORTS_DIR)

files = [f for f in os.listdir(REPORTS_DIR) if f.endswith((".xlsx", ".csv"))]

if files:
    selected_file = st.selectbox("Выберите отчёт:", files)

    file_path = os.path.join(REPORTS_DIR, selected_file)

    # Кнопка для скачивания
    with open(file_path, "rb") as f:
        st.download_button(
            label="⬇️ Скачать отчёт",
            data=f,
            file_name=selected_file,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if selected_file.endswith(".xlsx")
            else "text/csv",
        )

    # Отображение таблицы (если Excel или CSV)
    try:
        if selected_file.endswith(".xlsx"):
            df = pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path)

        st.subheader("Превью данных")
        st.dataframe(df.head(50), use_container_width=True)

    except Exception as e:
        st.error(f"Ошибка при чтении файла: {e}")

else:
    st.info("📂 Пока нет доступных отчётов. Загрузите файлы в dropbox и дождитесь анализа.")
