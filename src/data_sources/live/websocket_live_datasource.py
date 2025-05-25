from src.data_sources.live.live_datasource import LiveDataSource

import threading
import json
import websocket

class WebsocketLiveDataSource(LiveDataSource):
    def __init__(self, pair: str, ws_url: str):
        self.pair   = pair
        self.ws_url = ws_url

    def subscribe(self, callback):
        def _run():
            ws = websocket.create_connection(self.ws_url)
            # send any subscription message your feed needs
            ws.send(json.dumps({"op":"subscribe", "pair":self.pair}))
            while True:
                raw = ws.recv()
                msg = json.loads(raw)
                tick = {
                    "time":   int(msg["time"]),
                    "price":  float(msg["price"]),
                    "volume": int(msg.get("volume", 0)),
                    "pair":   self.pair
                }
                callback(tick)

        t = threading.Thread(target=_run, daemon=True)
        t.start()