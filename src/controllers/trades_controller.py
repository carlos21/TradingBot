import uuid
from datetime import datetime, timezone

from flask import abort, jsonify

from src.bars_loader import BarsLoader
from src.services.trade_manager import TradeManager
from src.utils.app_logger import ILogger


class TradesController:

    def __init__(self, bars_loader: BarsLoader, trade_manager: TradeManager, logger: ILogger, rr_ratio: float = 5.0, strategy=None):
        self.bars_loader = bars_loader
        self.trade_manager = trade_manager
        self.logger = logger
        self.rr_ratio = rr_ratio
        self.strategy = strategy

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
        bars = ds.load_historical_bars('1m')
        if bars:
            return bars[-1]['time']

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
        self.bars_loader.current_1m_index.get(pair, 0)

        # Resolve entry price from the loader's buffer or datasource
        entry_price = 0.0
        if self.bars_loader._1m_buffer:
            entry_price = self.bars_loader._1m_buffer[-1]['close']
        else:
            bars = self.bars_loader.data_source.load_historical_bars('1m')
            if bars:
                entry_price = bars[-1]['close']
            elif self.bars_loader._last_bar_close > 0:
                entry_price = self.bars_loader._last_bar_close
            else:
                abort(400, f"No price data available to open trade for {pair}")

        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            abort(400, 'Invalid stop loss; must be different from entry')

        take_profit = (
            entry_price + (self.rr_ratio * risk) if trade_type == 'long'
            else entry_price - (self.rr_ratio * risk)
        )

        # FIX: Use virtual time so the trade appears on the chart
        entry_time = self._get_virtual_now()
        self.logger.info(f"[TradesController] Opening Trade at Virtual Time: {entry_time}")

        # Generate trade_id so ZMQ command and DB record use the same ID
        trade_id = f"manual_{trade_type}_{uuid.uuid4().hex[:8]}"

        # SAFETY: Send ZMQ command FIRST, persist to DB only on success
        trade_for_zmq = {
            'trade_id': trade_id,
            'pair': pair,
            'type': trade_type,
            'entry': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'risk': risk,
            'rr_ratio': self.rr_ratio,
            'entry_time': entry_time,
            'source': 'manual',
        }
        try:
            self.trade_manager.trade_executor.on_trade_open(trade_for_zmq)
        except Exception as e:
            self.logger.error(f"[TradesController] ZMQ open failed for {trade_id}: {e}")
            abort(500, f"Failed to send order to platform: {e}")

        # Only persist if ZMQ command was queued successfully
        trade = self.trade_manager.open_trade(
            pair, trade_type, entry_price,
            stop_loss, take_profit, risk, entry_time, self.rr_ratio,
            source="manual", trade_id=trade_id
        )
        return jsonify(trade), 201

    def open_test_trade(self, pair: str, direction: str):
        # Resolve latest close price
        entry_price = 0.0
        if self.bars_loader._1m_buffer:
            entry_price = self.bars_loader._1m_buffer[-1]['close']
        else:
            bars = self.bars_loader.data_source.load_historical_bars('1m')
            if bars:
                entry_price = bars[-1]['close']
            elif self.bars_loader._last_bar_close > 0:
                entry_price = self.bars_loader._last_bar_close
            else:
                abort(400, f"No price data available to open test trade for {pair}")

        # Fixed test parameters: 20 points risk, strategy RR reward
        risk_points = 20.0
        rr_ratio = self.rr_ratio
        if direction == 'long':
            stop_loss = entry_price - risk_points
            take_profit = entry_price + (risk_points * rr_ratio)
            trade_type = 'long'
        else:
            stop_loss = entry_price + risk_points
            take_profit = entry_price - (risk_points * rr_ratio)
            trade_type = 'short'

        risk = abs(entry_price - stop_loss)
        entry_time = self._get_virtual_now()

        # Generate trade_id so ZMQ command and DB record use the same ID
        trade_id = f"test_{direction}_{uuid.uuid4().hex[:8]}"

        # SAFETY: Send ZMQ command FIRST, persist to DB only on success
        trade_for_zmq = {
            'trade_id': trade_id,
            'pair': pair,
            'type': trade_type,
            'entry': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'risk': risk,
            'rr_ratio': rr_ratio,
            'entry_time': entry_time,
            'source': 'test',
        }
        try:
            self.trade_manager.trade_executor.on_trade_open(trade_for_zmq)
        except Exception as e:
            self.logger.error(f"[TradesController] ZMQ open failed for test trade {trade_id}: {e}")
            abort(500, f"Failed to send order to platform: {e}")

        self.logger.info(f"[TradesController] Sent test {direction} trade {trade_id} to executor")

        # Only persist if ZMQ command was queued successfully
        trade = self.trade_manager.open_trade(
            pair, trade_type, entry_price,
            stop_loss, take_profit, risk, entry_time, rr_ratio,
            source="test", trade_id=trade_id
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
        else:
            bars = self.bars_loader.data_source.load_historical_bars('1m')
            if bars:
                exit_price = bars[-1]['close']
            elif self.bars_loader._last_bar_close > 0:
                exit_price = self.bars_loader._last_bar_close
            else:
                abort(400, f"No price data available to close trade {pair}")

        # FIX: Use virtual time
        exit_time = self._get_virtual_now()
        self.logger.info(f"[TradesController] Closing Trade {trade_id} at Virtual Time: {exit_time}")

        payload = self.trade_manager.close_trade(trade_id, exit_price, exit_time)

        # Sync strategy state so it knows the trade is closed
        if self.strategy:
            self.strategy.handle_broker_exit_fill(trade_id, exit_price, "CLOSE")

        return jsonify(payload), 200

    def close_all_trades(self, pair: str):
        """Close all open trades for a pair, reusing the same path as session-end / manual close."""
        open_trades = [t for t in self.trade_manager.open_trades if t['pair'] == pair]
        if not open_trades:
            return jsonify({'closed': [], 'message': 'No open trades to close'}), 200

        # Resolve exit price once for all trades
        exit_price = 0.0
        if self.bars_loader._1m_buffer:
            exit_price = self.bars_loader._1m_buffer[-1]['close']
        else:
            bars = self.bars_loader.data_source.load_historical_bars('1m')
            if bars:
                exit_price = bars[-1]['close']
            elif self.bars_loader._last_bar_close > 0:
                exit_price = self.bars_loader._last_bar_close
            else:
                abort(400, f"No price data available to close trades for {pair}")

        exit_time = self._get_virtual_now()
        self.logger.info(f"[TradesController] Closing ALL {len(open_trades)} open trade(s) for {pair} at Virtual Time: {exit_time}")

        closed = []
        failed = []
        for trade in list(open_trades):
            trade_id = trade['trade_id']
            try:
                payload = self.trade_manager.close_trade(trade_id, exit_price, exit_time)
                closed.append({'trade_id': trade_id, 'exit_price': exit_price, 'result': payload.get('result')})
                # Sync strategy state so it knows the trade is closed
                if self.strategy:
                    self.strategy.handle_broker_exit_fill(trade_id, exit_price, "CLOSE")
            except Exception as e:
                self.logger.error(f"[TradesController] Failed to close trade {trade_id}: {e}")
                failed.append({'trade_id': trade_id, 'error': str(e)})

        return jsonify({'closed': closed, 'failed': failed, 'count': len(closed)}), 200
