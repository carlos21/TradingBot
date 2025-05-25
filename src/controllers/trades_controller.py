from flask import abort, jsonify
from src.bars_loader import BarsLoader
from src.services.trade_manager import TradeManager
from datetime import datetime, timezone


class TradesController:

    def __init__(self, bars_loader: BarsLoader, trade_manager: TradeManager):
        self.bars_loader = bars_loader
        self.trade_manager = trade_manager

    def open_trade(self, pair, stop_loss, trade_type):
        # compute entry & risk based on last 1m
        idx = self.bars_loader.current_1m_index.get(pair, 0)
        if idx == 0:
            abort(400, f"No price data for pair {pair}")
        entry_price = self.bars_loader.all_1m_data[pair][idx-1]['close']
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            abort(400, 'Invalid stop loss; must be different from entry')
        take_profit = (
            entry_price + 4 * risk if trade_type == 'buy'
            else entry_price - 4 * risk
        )
        entry_time = datetime.now(timezone.utc).timestamp()
        trade = self.trade_manager.open_trade(
            pair, trade_type, entry_price,
            stop_loss, take_profit, risk, entry_time
        )
        return jsonify(trade), 201
    
    def close_trade(self, trade_id):
        # validate exists
        if not any(t['trade_id'] == trade_id for t in self.trade_manager.open_trades):
            abort(404, f"Trade id={trade_id} not found or already closed")
        # compute exit params
        trade = next(t for t in self.trade_manager.open_trades if t['trade_id'] == trade_id)
        pair = trade['pair']
        idx  = self.bars_loader.current_1m_index.get(pair, 0)
        if idx == 0:
            abort(400, f"No price data for pair {pair}")
        exit_price = self.bars_loader.all_1m_data[pair][idx-1]['close']
        exit_time  = datetime.now(timezone.utc).timestamp()

        payload = self.trade_manager.close_trade(trade_id, exit_price, exit_time)

        return jsonify(payload), 200