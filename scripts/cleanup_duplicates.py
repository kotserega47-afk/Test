# cleanup_duplicates.py
from sqlalchemy import func
from db.database import get_session
from db.models import CardEvent
from utils.logger import logger

def cleanup_duplicates():
    with get_session() as session:
        # ищем дубликаты по (card_id, operation_id)
        subquery = (
            session.query(
                CardEvent.card_id,
                CardEvent.operation_id,
                func.count(CardEvent.id).label("cnt")
            )
            .group_by(CardEvent.card_id, CardEvent.operation_id)
            .having(func.count(CardEvent.id) > 1)
            .all()
        )

        removed = 0
        for card_id, operation_id, cnt in subquery:
            # выбираем все дубли
            events = (
                session.query(CardEvent)
                .filter_by(card_id=card_id, operation_id=operation_id)
                .order_by(CardEvent.id)
                .all()
            )
            # оставляем первый, остальные удаляем
            for event in events[1:]:
                logger.warning(
                    f"Удаляем дубликат: event_id={event.id}, card_id={card_id}, operation_id={operation_id}"
                )
                session.delete(event)
                removed += 1

        session.commit()
        logger.info(f"✅ Очистка завершена. Удалено {removed} дубликатов.")

if __name__ == "__main__":
    cleanup_duplicates()
