# scripts/export_cards.py
import pandas as pd
from db.database import get_session
from db.models import Card

def export_cards_to_excel(file_path="cards_export.xlsx"):
    with get_session() as session:
        cards = session.query(Card).all()

        data = [
            {
                "id": c.id,
                "card_number": c.card_number,
                "pool_id": c.pool_id,
                "direction": c.direction,
                "balance": c.balance,
                "replenishment_method": c.replenishment_method,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "bakai_customer_id": c.bakai_customer_id,
                "first_success_at": c.first_success_at,
                "last_success_at": c.last_success_at,
                "first_error_at": c.first_error_at,
                "last_error_at": c.last_error_at,
                "total_success_amount": c.total_success_amount,
            }
            for c in cards
        ]

        df = pd.DataFrame(data)
        df.to_excel(file_path, index=False)
        print(f"✅ Данные выгружены в {file_path}")

if __name__ == "__main__":
    export_cards_to_excel()