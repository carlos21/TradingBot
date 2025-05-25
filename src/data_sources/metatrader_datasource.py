from src.data_sources.bars_datasource import BarsDataSource


class MetaTraderDataSource(BarsDataSource):
    def __init__(self, pair: str, creds: dict):
        self.pair = pair
        self.creds = creds

    def load_1m_bars(self):
        # pseudocode—use your MetaTrader API to fetch history:
        # conn = MTConnect(**self.creds)
        # raw = conn.get_history(self.pair, timeframe='M1', from=..., to=...)
        # return [ { 'time': ..., 'open': ..., … } for each bar ]
        raise NotImplementedError("MT source not yet wired up")