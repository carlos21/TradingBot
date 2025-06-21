from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tabulate import tabulate
from src.database.database import Trade

import os


SELECTED_COLUMNS = [
    "trade_id",
    "pair",
    "trade_type",
    "entry_price",
    "exit_price",
    "result",
]

# e.g. export DATABASE_URL="postgresql://user:pass@host/dbname"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./database.db")

engine = create_engine(DATABASE_URL, echo=False, future=True)
Session = sessionmaker(bind=engine, future=True)

def fetch_trades(cols):
    with Session() as session:
        # build query with only the selected columns
        q = session.query(*[getattr(Trade, c) for c in cols])
        return q.all()

def main():
    # validate columns
    all_cols = [c.name for c in Trade.__table__.columns]
    bad = [c for c in SELECTED_COLUMNS if c not in all_cols]
    if bad:
        raise ValueError(f"Unknown column(s) in SELECTED_COLUMNS: {bad}")

    rows = fetch_trades(SELECTED_COLUMNS)
    if not rows:
        print("No trades found.")
        return

    print(tabulate(rows, headers=SELECTED_COLUMNS, tablefmt="grid"))

if __name__ == "__main__":
    main()