from datetime import datetime, timezone

from flask import abort, jsonify

from src.bars_loader import BarsLoader
from src.dbexception import DBNotFoundException
from src.domain.repositories import LineRepository
from src.strategies.liquidity_v2.controllers.validators import LineInputValidator
from src.strategies.protocols import LiquidityStrategy
from src.utils.app_logger import ILogger


class LinesController:

    def __init__(self, line_repository: LineRepository, bars_loader: BarsLoader, liquidity_strategy: LiquidityStrategy, logger: ILogger):
        self.line_repository = line_repository
        self.bars_loader = bars_loader
        self.liquidity_strategy = liquidity_strategy
        self.logger = logger

    def list_lines(self, pair: str):
        lines = self.line_repository.list_lines(pair)
        return jsonify([{
            'id':            line.line_id,
            'pair':          line.pair,
            'price':         line.price,
            'creation_date': line.creation_date.isoformat()
        } for line in lines])

    def add_line(self, pair: str, price: float, creation_timestamp: float = None):
        ds = self.bars_loader.data_source
        # Backward compatibility: only enforce the single-pair restriction when
        # the data source still carries a hardcoded pair attribute.
        if hasattr(ds, 'pair') and pair != ds.pair:
            from flask import abort
            abort(400, f"Only pair '{ds.pair}' is supported")

        try:
            price = LineInputValidator.validate_price(price)
        except ValueError as e:
            from flask import abort
            abort(400, str(e))

        # 1. Resolve the Date
        if creation_timestamp is not None:
            try:
                creation_timestamp = LineInputValidator.validate_creation_timestamp(creation_timestamp)
            except ValueError as e:
                abort(400, str(e))
            c_date = datetime.fromtimestamp(creation_timestamp, tz=timezone.utc)
            self.logger.info(f"[LinesController] Using Provided Time: {c_date}")
        elif self.bars_loader._last_played_ts > 0:
            c_date = datetime.fromtimestamp(self.bars_loader._last_played_ts, tz=timezone.utc)
            self.logger.info(f"[LinesController] Using Loader Replay Time: {c_date}")
        else:
            c_date = datetime.now(tz=timezone.utc)
            self.logger.info(f"[LinesController] Loader not started -> Defaulting to current UTC: {c_date}")

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
            creation_timestamp=strat_date.timestamp(),
            pair=pair,
        )

        return jsonify({
            'id':            line.line_id,
            'pair':          line.pair,
            'price':         line.price,
            'creation_date': line.creation_date.isoformat()
        }), 201

    def get_line(self, line_id: str):
        """Get a single line by ID."""
        line = self.line_repository.get_line(line_id)
        if not line:
            abort(404, f"Line id={line_id} not found")
        return jsonify({
            'id': line.line_id,
            'pair': line.pair,
            'price': line.price,
            'creation_date': line.creation_date.isoformat()
        }), 200

    def update_line(self, line_id: str, price: float):
        """Update a line's price."""
        from src.dbexception import DBNotFoundException
        try:
            price = LineInputValidator.validate_price(price)
        except ValueError as e:
            abort(400, str(e))

        try:
            line = self.line_repository.update_line(line_id, price)
            # Update in strategy as well
            self.liquidity_strategy.update_strategy_line(line_id, price)
            if self.bars_loader.socketio:
                self.bars_loader.socketio.emit('line_updated', {
                    'id': line_id,
                    'price': price
                })
            return jsonify({
                'id': line.line_id,
                'pair': line.pair,
                'price': line.price,
                'creation_date': line.creation_date.isoformat()
            }), 200
        except DBNotFoundException:
            abort(404, f"Line id={line_id} not found")

    def delete_line(self, line_id):
        try:
            self.line_repository.delete_line(line_id)
            self.liquidity_strategy.remove_strategy_line(line_id)
            if self.bars_loader.socketio:
                self.bars_loader.socketio.emit('line_removed', {'id': line_id})
        except DBNotFoundException:
            abort(404, f"Line id={line_id} not found")
        return '', 204
