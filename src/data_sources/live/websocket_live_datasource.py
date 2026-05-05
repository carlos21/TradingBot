import json
import threading

import websocket

from src.data_sources.live.live_datasource import LiveDataSource


class WebsocketLiveDataSource(LiveDataSource):
    def __init__(self, pair: str, ws_url: str):
        self.pair   = pair
        self.ws_url = ws_url

    def load_historical_ticks(self):
        return []

    def subscribe(self, callback):
        def _run():
            ws = None
            try:
                ws = websocket.create_connection(self.ws_url)
                ws.send(json.dumps({"op":"subscribe", "pair":self.pair}))
                while True:
                    raw = ws.recv()
                    if not raw:
                        break
                    msg = json.loads(raw)
                    tick = {
                        "time":   int(msg["time"]),
                        "price":  float(msg["price"]),
                        "volume": int(msg.get("volume", 0)),
                        "pair":   self.pair
                    }
                    callback(tick)
            except Exception:
                pass  # Connection dropped or thread killed
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

        t = threading.Thread(target=_run, daemon=True)
        t.start()
