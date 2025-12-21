from typing import Callable, List
from datetime import datetime, timezone
from flask import jsonify, abort

from src.bars_loader import BarsLoader
from src.dbexception import DBNotFoundException
from src.repositories.lines_repository import LineRepository
from src.strategies.liquidity_strategy import LiquidityStrategy

class LinesController:

    def __init__(self, line_repository: LineRepository, bars_loader: BarsLoader, liquidity_strategy: LiquidityStrategy):
        self.line_repository = line_repository
        self.bars_loader = bars_loader
        self.liquidity_strategy = liquidity_strategy

    def list_lines(self, pair: str):
        lines = self.line_repository.list_lines(pair)
        return jsonify([{
            'id':            l.line_id,
            'pair':          l.pair,
            'price':         l.price,
            'creation_date': l.creation_date.isoformat()
        } for l in lines])
    
    def add_line(self, pair: str, price: float, creation_timestamp: float = None):
        ds = self.bars_loader.data_source
        supported_pair = getattr(ds, 'pair', None)
        if pair != supported_pair:
            abort(400, f"Only pair '{supported_pair}' is supported")

        # 1. Resolve the Date
        if creation_timestamp is not None:
            c_date = datetime.fromtimestamp(float(creation_timestamp), tz=timezone.utc)
            print(f"[LinesController] Using Provided Time: {c_date}")
        elif self.bars_loader._last_played_ts > 0:
            c_date = datetime.fromtimestamp(self.bars_loader._last_played_ts, tz=timezone.utc)
            print(f"[LinesController] Using Loader Replay Time: {c_date}")
        else:
            c_date = datetime.fromtimestamp(0, tz=timezone.utc)
            print(f"[LinesController] Loader not started -> Defaulting to Epoch 0 (1970)")

        # 2. Persist
        line = self.line_repository.insert_line(
            pair=pair,
            price=price,
            creation_date=c_date
        )

        # 3. Update Strategy
        # FIX: Double-check timezone awareness to prevent Local Time conversion issues
        strat_date = line.creation_date
        if strat_date.tzinfo is None:
            strat_date = strat_date.replace(tzinfo=timezone.utc)

        self.liquidity_strategy.add_strategy_line(
            line.line_id,
            line.price,
            creation_timestamp=strat_date.timestamp()
        )

        return jsonify({
            'id':            line.line_id,
            'pair':          line.pair,
            'price':         line.price,
            'creation_date': line.creation_date.isoformat()
        }), 201

    def delete_line(self, line_id):
        try:
            self.line_repository.delete_line(line_id)
            self.liquidity_strategy.remove_strategy_line(line_id)
            if self.bars_loader.socketio:
                self.bars_loader.socketio.emit('line_removed', {'id': line_id})
        except DBNotFoundException:
            abort(404, f"Line id={line_id} not found")
        return '', 204