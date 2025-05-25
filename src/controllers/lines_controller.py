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

    def list_lines(self):
        lines = self.line_repository.list_lines()
        return jsonify([{
            'id':            l.line_id,
            'pair':          l.pair,
            'price':         l.price,
            'direction':     l.direction,
            'creation_date': l.creation_date.isoformat()
        } for l in lines])
    
    def add_line(self, pair: str, price: float):
        idx = self.bars_loader.current_1m_index.get(pair, 0)
        last_close = None
        if idx > 0 and pair in self.bars_loader.all_1m_data:
            last_close = self.bars_loader.all_1m_data[pair][idx - 1]['close']
        direction = 'long'
        if last_close is not None:
            direction = 'short' if last_close < price else 'long'

        line = self.line_repository.insert_line(pair=pair, price=price, direction=direction)
        self.liquidity_strategy.add_strategy_line(line.line_id, line.price, line.direction)

        return jsonify({
            'id':            line.line_id,
            'pair':          line.pair,
            'price':         line.price,
            'direction':     line.direction,
            'creation_date': line.creation_date
        }), 201
    
    def delete_line(self, line_id):
        try:
            self.line_repository.delete_line(line_id)
            self.liquidity_strategy.remove_strategy_line(line_id)
        except DBNotFoundException:
            abort(404, f"Line id={line_id} not found")
        return '', 204