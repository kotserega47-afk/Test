# scripts/cleanup_db.py
import os
import yaml
from sqlalchemy import func
from db.database import get_session
from db.models import Card, CardEvent, CardDisableHistory
from utils.logger import logger
from load_data import parse_datetime, normalize_partner_name  # ✅ берём оттуда

def load_partner_exclusions(path="analysis_map.yaml"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return {
        normalize_partner_name(k): [
            (parse_datetime(period["start"]), parse_datetime(period["end"]))
            for period in v.get("exclude_periods", [])
        ]
        for k, v in config.get("partners", {}).items()
    }

def cleanup_duplicates_and_exclusions():
    removed_events = 0
    removed_cards = 0
    removed_disables = 0

    with get_session() as session:
        # -------------------------------
        # 1️⃣ Удаляем дубликаты карт
        # -------------------------------
        dup_cards = (
            session.query(Card.card_number)
            .group_by(Card.card_number)
            .having(func.count(Card.id) > 1)
            .all()
        )
        for (card_number,) in dup_cards:
            cards = session.query(Card).filter_by(card_number=card_number).order_by(Card.id).all()
            # оставляем первую карту, остальные удаляем
            for card in cards[1:]:
                session.delete(card)
                removed_cards += 1

        # -------------------------------
        # 2️⃣ Удаляем дубликаты событий
        # -------------------------------
        dup_events = (
            session.query(CardEvent.card_id, CardEvent.operation_id)
            .group_by(CardEvent.card_id, CardEvent.operation_id)
            .having(func.count(CardEvent.id) > 1)
            .all()
        )
        for card_id, op_id in dup_events:
            events = (
                session.query(CardEvent)
                .filter_by(card_id=card_id, operation_id=op_id)
                .order_by(CardEvent.id)
                .all()
            )
            for ev in events[1:]:
                session.delete(ev)
                removed_events += 1

        # -------------------------------
        # 3️⃣ Чистим отключения по новым диапазонам
        # -------------------------------
        exclusions = load_partner_exclusions()
        disables = session.query(CardDisableHistory).all()

        for d in disables:
            card = session.query(Card).filter_by(card_number=d.card_number).first()
            if not card:
                continue

            partner_norm = normalize_partner_name(card.pool_id or "")
            periods = exclusions.get(partner_norm, [])
            if not periods:
                continue

            # находим событие, связанное с этой картой
            event = (
                session.query(CardEvent)
                .filter_by(card_id=card.id)
                .order_by(CardEvent.created_at.asc())
                .first()
            )
            if not event:
                continue

            created_at = event.created_at
            if any(start <= created_at <= end for start, end in periods):
                logger.info(
                    f"Удаляем отключение карты {d.card_number} "
                    f"(created_at={created_at}) т.к. попадает в исключение"
                )
                session.delete(d)
                removed_disables += 1

        session.commit()

    logger.info(
        f"✅ Очистка завершена. "
        f"Удалено {removed_cards} карт, {removed_events} событий, {removed_disables} отключений."
    )

if __name__ == "__main__":
    cleanup_duplicates_and_exclusions()
