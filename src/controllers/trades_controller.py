from flask import abort, jsonify
from src.bars_loader import BarsLoader
from src.services.trade_manager import TradeManager
from src.utils.app_logger import ILogger
from datetime import datetime, timezone


class TradesController:

    def __init__(self, bars_loader: BarsLoader, trade_manager: TradeManager, logger: ILogger):
        self.bars_loader = bars_loader
        self.trade_manager = trade_manager
        self.logger = logger

    def _get_virtual_now(self) -> float:
        """
        Resolve the current 'virtual' timestamp.
        1. Loader's last played tick (during replay).
        2. Last bar in data source history (if paused/stopped).
        3. System time (fallback).
        """
        # 1. Active Replay Time
        if self.bars_loader._last_played_ts > 0:
            return self.bars_loader._last_played_ts
        
        # 2. Data Source History End (if we are just viewing a static chart)
        ds = self.bars_loader.data_source
        played = getattr(ds, '_played_bars', [])
        if played:
            return played[-1]['time']
            
        # 3. System Time (Fallback)
        return datetime.now(timezone.utc).timestamp()

    def list_trades(self, pair: str):
        """Return all trades (open and closed) for the pair."""
        trades = self.trade_manager.trade_repository.list_trades(pair)
        
        # --- DEBUG LOG ---
        self.logger.info(f"[TradesController] list_trades('{pair}') found {len(trades)} trades.")
        for i, t in enumerate(trades):
            self.logger.info(f"  [{i}] ID={t.trade_id} EntryTime={t.entry_time.timestamp()} ExitTime={t.exit_time.timestamp() if t.exit_time else 'None'}")
        # -----------------

        data = []
        for t in trades:
            data.append({
                'trade_id':    t.trade_id,
                'pair':        t.pair,
                'type':        t.trade_type,
                'entry':       t.entry_price,
                'stop_loss':   t.stop_loss,
                'take_profit': t.take_profit,
                'risk':        t.risk,
                'risk_dollars': t.risk_dollars,
                'risk_pct':    t.risk_pct,
                'contracts':   t.contracts,
                'entry_time':  t.entry_time.timestamp(),
                'exit_price':  t.exit_price,
                'exit_time':   t.exit_time.timestamp() if t.exit_time else None,
                'result':      t.result,
                'status':      'closed' if t.exit_time else 'open'
            })
        return jsonify(data), 200

    def open_trade(self, pair, stop_loss, trade_type):
        # compute entry & risk based on last 1m
        idx = self.bars_loader.current_1m_index.get(pair, 0)
        
        # Resolve entry price from the loader's buffer or datasource
        entry_price = 0.0
        if self.bars_loader._1m_buffer:
            entry_price = self.bars_loader._1m_buffer[-1]['close']
        elif hasattr(self.bars_loader.data_source, '_played_bars') and self.bars_loader.data_source._played_bars:
            entry_price = self.bars_loader.data_source._played_bars[-1]['close']
        else:
            abort(400, f"No price data available to open trade for {pair}")

        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            abort(400, 'Invalid stop loss; must be different from entry')
        
        take_profit = (
            entry_price + 4 * risk if trade_type == 'buy'
            else entry_price - 4 * risk
        )
        
        # FIX: Use virtual time so the trade appears on the chart
        entry_time = self._get_virtual_now()
        self.logger.info(f"[TradesController] Opening Trade at Virtual Time: {entry_time}")
        
        trade = self.trade_manager.open_trade(
            pair, trade_type, entry_price,
            stop_loss, take_profit, risk, entry_time
        )
        return jsonify(trade), 201
    
    def close_trade(self, trade_id):
        if not any(t['trade_id'] == trade_id for t in self.trade_manager.open_trades):
            abort(404, f"Trade id={trade_id} not found or already closed")
        
        trade = next(t for t in self.trade_manager.open_trades if t['trade_id'] == trade_id)
        pair = trade['pair']
        
        # Resolve exit price
        exit_price = 0.0
        if self.bars_loader._1m_buffer:
            exit_price = self.bars_loader._1m_buffer[-1]['close']
        elif hasattr(self.bars_loader.data_source, '_played_bars') and self.bars_loader.data_source._played_bars:
            exit_price = self.bars_loader.data_source._played_bars[-1]['close']
        else:
            abort(400, f"No price data available to close trade {pair}")

        # FIX: Use virtual time
        exit_time = self._get_virtual_now()
        self.logger.info(f"[TradesController] Closing Trade {trade_id} at Virtual Time: {exit_time}")

        payload = self.trade_manager.close_trade(trade_id, exit_price, exit_time)

        return jsonify(payload), 200