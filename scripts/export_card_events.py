# scripts/export_card_events.py
import pandas as pd
from db.database import get_session
from db.models import CardEvent, Card, ErrorType

def export_card_events_to_excel(file_path="card_events_export.xlsx"):
    with get_session() as session:
        events = session.query(CardEvent).all()

        data = []
        for e in events:
            data.append({
                "id": e.id,
                "card_id": e.card_id,
                "card_number": e.card.card_number if e.card else None,
                "status": e.status,
                "amount": e.amount,
                "operation_id": e.operation_id,
                "created_at": e.created_at,
                "error_code": e.error.code if e.error else None,
                "error_description": e.error.description if e.error else None,
                "source_file": e.source_file,
            })

        df = pd.DataFrame(data)
        df.to_excel(file_path, index=False)
        print(f"✅ Данные выгружены в {file_path}")

if __name__ == "__main__":
    export_card_events_to_excel()
