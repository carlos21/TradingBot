from src.bars_loader import BarsLoader
from src.dbexception import DBNotFoundException
from src.repositories.lines_repository import LineRepository
from flask import jsonify, abort

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
    
    def add_line(self, pair: str, price: float):
        # ensure only the configured pair is supported
        ds = self.bars_loader.data_source
        supported_pair = getattr(ds, 'pair', None)
        if pair != supported_pair:
            abort(400, f"Only pair '{supported_pair}' is supported")

        # use in-memory played bars to get the last close price
        played = getattr(ds, '_played_bars', None)
        if not played or len(played) == 0:
            abort(400, "No bars have been replayed yet to determine last close price")

        # persist the line and register it with the strategy
        line = self.line_repository.insert_line(
            pair=pair,
            price=price
        )
        self.liquidity_strategy.add_strategy_line(
            line.line_id,
            line.price
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
        except DBNotFoundException:
            abort(404, f"Line id={line_id} not found")
        return '', 204