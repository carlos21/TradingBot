import threading, time
from src.data_sources.bars_datasource import BarsDataSource
from src.data_sources.live.live_datasource import LiveDataSource

class CSVReplayTickSource(LiveDataSource):

    def __init__(self, bars_source: BarsDataSource, speed_multiplier=1.0):
        self.pair            = bars_source.pair
        self.rows            = bars_source.load_1m_bars()
        self.speed_multiplier = speed_multiplier

    def load_historical_ticks(self):
        return []

    def subscribe(self, callback):
        def _replay():
            for i, bar in enumerate(self.rows):
                if i>0:
                    prev = self.rows[i-1]
                    delta = (bar['time'] - prev['time'])/self.speed_multiplier
                    time.sleep(delta)
                callback(bar)
        threading.Thread(target=_replay, daemon=True).start()