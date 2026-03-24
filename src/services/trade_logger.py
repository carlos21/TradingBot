from src.repositories.trades_repository import TradeRepository


class TradeLogger:
    def __init__(self, trade_repository: TradeRepository):
        self._repo = trade_repository

    def log(self, trade_id: str, event: str, msg: str):
        self._repo.append_trade_log(trade_id, event, msg)

    @staticmethod
    def format_logs(trade_data) -> str:
        """Human-readable formatted output for a trade's lifecycle."""
        tid = trade_data.trade_id[:8] if trade_data.trade_id else "????????"
        direction = trade_data.trade_type or "?"
        pair = trade_data.pair or "?"

        header = (
            f"\n{'=' * 55}\n"
            f"Trade {tid} ({direction} {pair})\n"
            f"Entry: {trade_data.entry_price:.2f}  "
            f"SL: {trade_data.stop_loss:.2f}  "
            f"TP: {trade_data.take_profit:.2f}  "
            f"Risk: {trade_data.risk:.2f}\n"
            f"{'=' * 55}"
        )

        logs = trade_data.logs or []
        if not logs:
            return header + "\n  (no log entries)\n" + "=" * 55

        lines = [header]
        for entry in logs:
            ts = entry.get("ts", "")
            # Extract time portion (HH:MM:SS) from ISO timestamp
            time_part = ts[11:19] if len(ts) >= 19 else ts
            event = entry.get("event", "")
            msg = entry.get("msg", "")
            lines.append(f"{time_part}  {event:<15s}{msg}")
        lines.append("=" * 55)
        return "\n".join(lines)
