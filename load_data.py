# load_data.py
import pandas as pd
from db.database import SessionLocal
from db.models import Card, CardEvent, ErrorType
from datetime import datetime

def process_conversion(card_df: pd.DataFrame, conversion_df: pd.DataFrame, session):
    """
    Обрабатывает данные при конверсии:
    - Добавляет новые карты или обновляет существующие
    - Добавляет новые события с snapshot состояния карты
    - Пересчитывает агрегаты карты (жизненный цикл и сумму успешных операций)
    """
    # 1️⃣ Добавление или обновление карт
    for _, row in card_df.iterrows():
        card = session.query(Card).filter_by(card_number=row['Карта']).first()
        if not card:
            card = Card(
                card_number=row['Карта'],
                pool_id=row.get('Пул'),
                direction=row.get('Направление'),
                balance=row.get('Баланс'),
                replenishment_method=row.get('Метод пополнения'),
                first_name=row.get('Имя'),
                last_name=row.get('Фамилия'),
                bakai_customer_id=row.get('Bakai customer_id')
            )
            session.add(card)
        else:
            # Обновляем карту текущей информацией
            card.pool_id = row.get('Пул') or card.pool_id
            card.direction = row.get('Направление') or card.direction
            card.balance = row.get('Баланс') if row.get('Баланс') is not None else card.balance
            card.replenishment_method = row.get('Метод пополнения') or card.replenishment_method
            card.first_name = row.get('Имя') or card.first_name
            card.last_name = row.get('Фамилия') or card.last_name
            card.bakai_customer_id = row.get('Bakai customer_id') or card.bakai_customer_id

    session.commit()

    # 2️⃣ Добавление новых событий
    for _, row in conversion_df.iterrows():
        card = session.query(Card).filter_by(card_number=row['Карта']).first()
        if not card:
            # На всякий случай создаём карту минимально
            card = Card(card_number=row['Карта'])
            session.add(card)
            session.flush()

        # Проверка дубля по operation_id
        existing_event = session.query(CardEvent).filter_by(operation_id=row['ID операции']).first()
        if existing_event:
            continue

        # Ошибка
        error_id = None
        if row['Статус'].lower() == 'error' and row.get('Инфо'):
            error = session.query(ErrorType).filter_by(code=row['Инфо']).first()
            if not error:
                error = ErrorType(code=row['Инфо'], description=row['Инфо'])
                session.add(error)
                session.flush()
            error_id = error.id

        created_at = pd.to_datetime(row['Дата/Время создания'])

        # Создаём событие с snapshot
        event = CardEvent(
            card_id=card.id,
            status=row['Статус'].lower(),
            amount=row['Сумма'] if row['Статус'].lower() == 'success' else None,
            operation_id=row['ID операции'],
            created_at=created_at,
            error_id=error_id,
            source_file=row.get('source_file', None),
            snapshot_data={
                "direction": card.direction,
                "balance": card.balance,
                "replenishment_method": card.replenishment_method,
                "first_name": card.first_name,
                "last_name": card.last_name,
                "bakai_customer_id": card.bakai_customer_id,
                "pool_id": card.pool_id
            }
        )
        session.add(event)

    session.commit()

    # 3️⃣ Пересчёт агрегатов карты
    cards_to_update = session.query(Card).all()
    for card in cards_to_update:
        # Успешные операции
        success_events = session.query(CardEvent).filter_by(card_id=card.id, status='success').all()
        if success_events:
            card.first_success_at = min(e.created_at for e in success_events)
            card.last_success_at = max(e.created_at for e in success_events)
            card.total_success_amount = sum(e.amount for e in success_events if e.amount)
        else:
            card.first_success_at = None
            card.last_success_at = None
            card.total_success_amount = 0

        # Ошибки
        error_events = session.query(CardEvent).filter_by(card_id=card.id, status='error').all()
        if error_events:
            card.first_error_at = min(e.created_at for e in error_events)
            card.last_error_at = max(e.created_at for e in error_events)
        else:
            card.first_error_at = None
            card.last_error_at = None

    session.commit()


# 🔹 Пример вызова
if __name__ == "__main__":
    session = SessionLocal()
    card_df = pd.read_excel("cards.xlsx")
    conversion_df = pd.read_excel("conversion.xlsx")
    process_conversion(card_df, conversion_df, session)
    print("Данные успешно обработаны, карты обновлены, snapshot сохранён!")
