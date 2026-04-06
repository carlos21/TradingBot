"""NinjaTrader integration HTTP routes - Live mode only."""

from datetime import datetime, timezone
from flask import Flask, jsonify, request, abort
from src.repositories.trades_repository import TradeRepository
from src.services.trade_logger import TradeLogger
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2
from src.data_sources.ninjatrader_datasource import NinjaTraderDataSource
from src.notifier import Notifier


def register_nt_routes(
    app: Flask,
    data_source: NinjaTraderDataSource,
    trade_manager: TradeManager,
    trades_repo: TradeRepository,
    trade_logger: TradeLogger,
    strategy: LiquidityStrategyV2,
    pair: str,
    notifier: Notifier,
):
    """Register NinjaTrader integration routes.
    
    These routes are only used in live mode when connected to NinjaTrader.
    
    Args:
        app: Flask application instance
        data_source: NinjaTrader data source
        trade_manager: Manager for trade lifecycle
        trades_repo: Repository for trade persistence
        trade_logger: Logger for trade events
        strategy: Trading strategy instance
        pair: Trading pair
        notifier: Notification service
    """
    
    # E2E test state tracking
    _test_sequences: dict = {}

    @app.route('/api/nt/await_command', methods=['GET'])
    def nt_await_command():
        """Long-poll: NinjaTrader hangs here until Python has a command."""
        cmd = data_source.await_command(timeout=30.0)
        if cmd:
            print(f"[NT LongPoll] Sending command: {cmd}", flush=True)
            return jsonify(cmd)
        return jsonify({'command': None})

    @app.route('/api/nt/refresh_start', methods=['POST'])
    def nt_refresh_start():
        print("[NT Ingest] REFRESH_START received", flush=True)
        data_source.handle_refresh_start()
        return jsonify({'ok': True})

    @app.route('/api/nt/bars', methods=['POST'])
    def nt_ingest_bars():
        bars = request.get_json()
        if not isinstance(bars, list):
            abort(400, 'Expected JSON array of bar objects')
        data_source.ingest_bars(bars)
        print(f"[NT Ingest] Received {len(bars)} bars, total={len(data_source._historical_bars)}", flush=True)
        return jsonify({'ok': True, 'count': len(bars)})

    @app.route('/api/nt/history_end', methods=['POST'])
    def nt_history_end():
        print(f"[NT Ingest] HISTORY_END received", flush=True)
        data_source.mark_history_complete()
        return jsonify({'ok': True})

    @app.route('/api/nt/tick', methods=['POST'])
    def nt_ingest_tick():
        tick = request.get_json()
        data_source.ingest_tick(tick)
        return jsonify({'ok': True})

    @app.route('/api/nt/bar', methods=['POST'])
    def nt_ingest_live_bar():
        bar = request.get_json()
        ts = int(bar.get('time', 0))
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')
        print(f"[NT /bar] COMPLETED bar time={dt} ({ts}) O={bar.get('open')} H={bar.get('high')} L={bar.get('low')} C={bar.get('close')}", flush=True)
        data_source.ingest_live_bar(bar)
        return jsonify({'ok': True})

    @app.route('/api/nt/partial', methods=['POST'])
    def nt_ingest_partial():
        bar = request.get_json()
        bar['partial'] = True
        data_source.ingest_live_bar(bar)
        return jsonify({'ok': True})

    @app.route('/api/nt/test_connection', methods=['POST'])
    def nt_test_connection():
        """Simple ping/pong to verify HTTP connectivity."""
        return jsonify({'ok': True, 'ts': datetime.now(tz=timezone.utc).isoformat()})

    @app.route('/api/nt/run_e2e_test', methods=['POST'])
    def nt_run_e2e_test():
        """Start an E2E test scenario. Body: {"scenario": "tp_hit"|"sl_hit"|"session_end"}"""
        data = request.get_json() or {}
        scenario = data.get('scenario', 'tp_hit')
        if scenario not in ('tp_hit', 'sl_hit', 'session_end'):
            abort(400, 'scenario must be tp_hit, sl_hit, or session_end')

        entry_price = 21000.0
        risk = 80.0
        tp_rr = 1.0  # TP at 1R for test simplicity
        sl = entry_price - risk       # 20920.0
        tp = entry_price + risk * tp_rr  # 21080.0
        now_dt = datetime.now(tz=timezone.utc)

        trade_data = trades_repo.insert_trade(
            pair=pair, trade_type="long",
            entry_price=entry_price, stop_loss=sl,
            take_profit=tp, risk=risk,
            entry_time=now_dt,
        )
        trade_id = trade_data.trade_id
        trade_manager.open_trades.append({
            'trade_id': trade_id, 'pair': pair, 'type': 'long',
            'entry': entry_price, 'stop_loss': sl, 'take_profit': tp,
            'risk': risk, 'entry_time': now_dt.timestamp(),
            'status': 'open'
        })

        trade_logger.log(trade_id, "TEST", f"E2E test started: {scenario}")

        data_source.enqueue_command({
            "command": "place_order",
            "trade_id": trade_id,
            "pair": pair,
            "direction": "long",
            "entry_price": entry_price,
            "sl_points": risk,
            "rr_ratio": tp_rr,
            "test": True,
            "scenario": scenario,
        })

        _test_sequences[trade_id] = {
            "stage": "awaiting_entry_fill",
            "scenario": scenario,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
        }

        print(f"[E2E Test] Started scenario={scenario} trade_id={trade_id}", flush=True)
        return jsonify({
            "ok": True, "trade_id": trade_id, "scenario": scenario,
            "entry_price": entry_price, "sl": sl, "tp": tp
        })

    @app.route('/api/nt/test_result/<string:test_trade_id>', methods=['GET'])
    def nt_test_result(test_trade_id):
        """Check the result of an E2E test by trade_id."""
        logs = trades_repo.get_trade_logs(test_trade_id)
        events = [l['event'] for l in logs]

        has_error = any("ERROR" in e for e in events)

        # Check trade is actually closed in DB (more reliable than log events
        # which can be lost to concurrent JSON column writes)
        all_trades = trades_repo.list_trades(pair)
        trade = next((t for t in all_trades if t.trade_id == test_trade_id), None)
        trade_closed = trade is not None and trade.exit_time is not None

        # Key command events that must be present
        has_key_events = all(e in events for e in
                            ["NT:ORDER", "NT_ENTRY_FILL", "NT:MODIFY"])
        passed = trade_closed and has_key_events and not has_error

        formatted = TradeLogger.format_logs(trade) if trade else "(trade not found)"

        return jsonify({
            "passed": passed,
            "trade_closed": trade_closed,
            "has_errors": has_error,
            "events_found": events,
            "trade_id": test_trade_id,
            "formatted_logs": formatted,
        })

    @app.route('/api/nt/trade_log', methods=['POST'])
    def nt_trade_log():
        """Accept log entries from NinjaTrader (NT:ORDER, NT:FILL, NT:MODIFY, etc.)."""
        data = request.get_json()
        tid = data.get('trade_id')
        event = data.get('event')
        msg = data.get('msg', '')
        if not tid or not event:
            abort(400, 'trade_id and event are required')
        trade_logger.log(tid, event, msg)

        if "ERROR" in event.upper():
            notifier.send(f"[NinjaTrader] {event}: {msg} (trade {tid})")

        # E2E test state machine: advance after NT:MODIFY acknowledgement
        if tid in _test_sequences:
            seq = _test_sequences[tid]
            if event == "NT:MODIFY" and seq["stage"] == "awaiting_modify_ack":
                if seq["scenario"] == "session_end":
                    seq["stage"] = "awaiting_close_fill"
                    data_source.enqueue_command({"command": "close_order", "trade_id": tid, "test": True})
                    trade_logger.log(tid, "CMD_SENT", "close_order -> NinjaTrader (session end test)")
                    print(f"[E2E Test] {tid}: close_order enqueued (session_end)", flush=True)
                else:
                    seq["stage"] = "awaiting_exit_fill"
                    print(f"[E2E Test] {tid}: awaiting exit fill from NT ({seq['scenario']})", flush=True)

        return jsonify({'ok': True})

    @app.route('/api/nt/positions', methods=['POST'])
    def nt_positions_sync():
        """Reconcile broker positions with DB after reconnect/restart."""
        positions = request.get_json()
        if not isinstance(positions, list):
            abort(400, 'Expected JSON array of position objects')

        broker_ids = {p['trade_id'] for p in positions}
        db_open = [t for t in trades_repo.list_trades(pair) if t.exit_time is None]
        db_open_ids = {t.trade_id for t in db_open}

        reconciled = []

        # DB has open trade but broker does not → closed offline
        for t in db_open:
            if t.trade_id not in broker_ids:
                print(f"[PositionSync] Trade {t.trade_id} closed offline (not on broker)")
                trade_logger.log(t.trade_id, "POSITION_SYNC", "Closed offline (not on broker)")
                trades_repo.close_trade(
                    trade_id=t.trade_id,
                    exit_price=0,
                    exit_time=datetime.now(tz=timezone.utc),
                    result=0.0,
                    result_type="SP"
                )
                # Remove from trade_manager in-memory
                trade_manager.open_trades = [
                    ot for ot in trade_manager.open_trades if ot['trade_id'] != t.trade_id
                ]
                reconciled.append({'trade_id': t.trade_id, 'action': 'closed_offline'})

        # Broker has position but DB does not → orphan
        for p in positions:
            if p['trade_id'] not in db_open_ids:
                print(f"[PositionSync] WARNING: Orphan position on broker: {p['trade_id']}")
                reconciled.append({'trade_id': p['trade_id'], 'action': 'orphan_warning'})

        # Matching → already resumed by TradeManager._load_open_trades_from_db
        for p in positions:
            if p['trade_id'] in db_open_ids:
                reconciled.append({'trade_id': p['trade_id'], 'action': 'resumed'})

        print(f"[PositionSync] Reconciliation complete: {len(reconciled)} items")
        return jsonify({'ok': True, 'reconciled': reconciled})

    @app.route('/api/nt/entry_fill', methods=['POST'])
    def nt_entry_fill():
        """NinjaTrader reports the actual entry fill price and real SL/TP."""
        data = request.get_json()
        trade_id = data.get('trade_id')
        entry_price = float(data.get('entry_price', 0))
        broker_sl = float(data['stop_loss']) if data.get('stop_loss') is not None else None
        broker_tp = float(data['take_profit']) if data.get('take_profit') is not None else None

        if not trade_id:
            abort(400, 'trade_id is required')

        print(f"[NT EntryFill] trade_id={trade_id} entry={entry_price} "
              f"SL={broker_sl} TP={broker_tp}", flush=True)
        trade_manager.handle_broker_entry_fill(trade_id, entry_price, broker_sl, broker_tp)

        # Also update entry/SL/TP in strategy's open_trades list
        for t in strategy.open_trades:
            if t.get('trade_id') == trade_id:
                t['entry'] = entry_price
                if broker_sl is not None:
                    t['stop_loss'] = broker_sl
                if broker_tp is not None:
                    t['take_profit'] = broker_tp
                break

        # E2E test state machine: after entry fill, move SL to breakeven
        if trade_id in _test_sequences and _test_sequences[trade_id]["stage"] == "awaiting_entry_fill":
            seq = _test_sequences[trade_id]
            seq["stage"] = "awaiting_modify_ack"
            # Update seq with broker's real values
            seq["entry_price"] = entry_price
            if broker_tp is not None:
                seq["tp"] = broker_tp
            new_sl = entry_price  # breakeven
            for t in trade_manager.open_trades:
                if t.get('trade_id') == trade_id:
                    t['stop_loss'] = new_sl
                    break
            trades_repo.update_stop_loss(trade_id, new_sl)
            trade_logger.log(trade_id, "SL_UPDATE", f"SL moved to breakeven {new_sl}")
            data_source.enqueue_command({
                "command": "modify_order", "trade_id": trade_id, "stop_loss": new_sl,
                "test": True, "scenario": seq["scenario"],
                "entry_price": seq["entry_price"], "tp": seq["tp"],
            })
            trade_logger.log(trade_id, "CMD_SENT", "modify_order -> NinjaTrader (breakeven)")
            print(f"[E2E Test] {trade_id}: modify_order enqueued (breakeven)", flush=True)

        return jsonify({'ok': True})

    @app.route('/api/nt/fill', methods=['POST'])
    def nt_fill():
        """NinjaTrader reports a broker fill (SL, TP, or close)."""
        data = request.get_json()
        trade_id = data.get('trade_id')
        exit_price = float(data.get('exit_price', 0))
        result_type = data.get('result_type')  # "SL", "TP", or None

        if not trade_id:
            abort(400, 'trade_id is required')

        print(f"[NT Fill] trade_id={trade_id} exit_price={exit_price} type={result_type}", flush=True)
        trade_manager.handle_broker_fill(trade_id, exit_price, result_type)

        # Also remove from strategy's open_trades list
        strategy.open_trades = [
            t for t in strategy.open_trades if t.get('trade_id') != trade_id
        ]

        # Clean up E2E test state
        _test_sequences.pop(trade_id, None)

        return jsonify({'ok': True})
