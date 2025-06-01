from datetime import datetime
from dateutil import parser
from zoneinfo import ZoneInfo
import threading
import time

from flask_socketio import SocketIO
from src.data_sources.combined_datasource import CombinedDataSource


class BarsConfig:
    """Configuration for bar loading: time formats, timezones, CSV files, and initial date window."""
    DEFAULT_TIME_FMT    = '%d/%m/%Y %H:%M:%S'
    DEFAULT_TIMEZONE    = 'UTC'
    TIME_FORMATS        = {
        'EURUSD': '%Y.%m.%d %H:%M',
        'NQ':      '%d/%m/%Y %H:%M:%S',
    }
    PAIR_TIMEZONES      = {
        'EURUSD': 'Europe/London',
        'NQ':      'America/Chicago',
    }
    CSV_FILES           = {
        'EURUSD': 'csvs/EURUSD_2019.csv',
        'NQ':      'csvs/NQ_2024.csv',
    }
    INITIAL_START       = parser.parse("2024-08-01T00:00:00Z")
    INITIAL_END         = parser.parse("2024-08-30T23:59:59Z")

    # strategy parameters
    STOP_LOSS_CONFIG    = {
        'EURUSD': 0.0004,  # 4 pips
        'NQ':      10       # 10 points
    }
    MAX_BOUNCE_CONFIG   = {
        'EURUSD': 0.0020,   # 20 pips
        'NQ':      50       # 50 points
    }


class BarsLoader:
    """
    Drives everything off a single 1m loop:
    - Builds TF bars by grouping exactly `group_size` consecutive 1m bars aligned to real clock windows.
    - Emits 1m bars immediately when TF='1m'; otherwise groups and emits larger bars.
    - Checks each incoming bar for SL/TP (via `bar_callback`).
    - Uses a single CombinedDataSource to supply historical bars and live ticks.
    - Ensures only one replay thread is running at a time; pressing Pause stops it.
    - On Pause and Resume, it picks up exactly where it left off—never re‐using or skipping 1m bars,
      and always grouping into the correct clock‐aligned window.
    """

    def __init__(
        self,
        config: BarsConfig,
        data_source: CombinedDataSource,
        socketio: SocketIO,
        strategy=None,
        bar_callback: callable = None
    ):
        self.config       = config
        self.data_source  = data_source
        self.socketio     = socketio
        self.strategy     = strategy
        self.bar_callback = bar_callback
        self.stream_lock  = threading.Lock()

        # Load full 1m history for REST slicing and pointer tracking
        self.pair      = data_source.pair
        self.raw_1m    = data_source.load_historical_bars()

        # Streaming / replay state
        self.current_1m_index     = 0    # Next raw_1m index to process
        self.streaming_1m         = False
        self._from_time           = 0    # Timestamp cutoff (for initial start)

        # Buffer for grouping 1m bars into TF bars (time‐aligned)
        self._1m_buffer           = []
        self._current_group_start = None  # UNIX‐epoch start of the current N‐minute window

        # Current timeframe and grouping size (in minutes)
        self.current_tf  = '5m'
        self.group_size  = 5  # default: 5m => group_size=5

        # Thread‐management flags
        self._replay_thread = None
        self._stop_replay   = threading.Event()

    def set_timeframe(self, tf: str):
        """
        Set user TF like '1m','5m','15m','1h','4h', etc. Compute how many
        consecutive minutes are needed (group_size), clear any partial buffer,
        FORZAR un restart completo para que el próximo start() tome este nuevo TF.
        """
        # 1) Si hay un hilo corriendo, deténlo inmediatamente:
        if self._replay_thread and self._replay_thread.is_alive():
            print("[DEBUG TF] Deteniendo hilo activo para cambiar timeframe...")
            self._stop_replay.set()
            self.streaming_1m = False
            # Esperar a que termine
            self._replay_thread.join()
            print("[DEBUG TF] Hilo detenido.")

        # 2) Reset de estado global: buffer, índice, ventana:
        with self.stream_lock:
            self._1m_buffer.clear()
            self._current_group_start = None

        # 3) Asignar el nuevo current_tf y actualizar group_size:
        self.current_tf = tf
        unit = tf[-1]
        num  = int(tf[:-1])
        minutes = num * (60 if unit == 'h' else 1)
        self.group_size = max(1, minutes)

        # 4) Siempre reiniciamos el índice a 0 para que el próximo start() arranque "fresco":
        self.current_1m_index = 0

        print(f"[DEBUG TF] set_timeframe: tf={tf}  group_size={self.group_size}  buffer_cleared  "
              f"current_1m_index={self.current_1m_index}")

    def start(self, from_time: int = None):
        """
        Begin streaming. 'from_time' is the UNIX timestamp to skip all bars <= that.
        Siempre que se llame a start(), asumimos que el buffer e índice ya están alineados
        (porque set_timeframe acaba de resetearlos). Si current_1m_index == 0, alineamos a `from_time`.
        """
        # 1) Record the cutoff timestamp
        if from_time is not None:
            self._from_time = from_time

        # 2) Si current_1m_index == 0, significa “primer Play” tras cambiar timeframe o inicio:
        if self.current_1m_index == 0:
            print(f"[DEBUG START] from_time actual = {self._from_time}")
            idx = next(
                (i for i, b in enumerate(self.raw_1m) if b['time'] > self._from_time),
                len(self.raw_1m)
            )
            print(f"[DEBUG START] idx buscado raw_1m = {idx}, timestamp de esa fila = "
                  f"{self.raw_1m[idx]['time'] if idx < len(self.raw_1m) else 'fin'}")
            self.current_1m_index = idx
            with self.stream_lock:
                self._1m_buffer.clear()
                self._current_group_start = None

        # 3) Clear the stop flag so the new thread can run
        self._stop_replay.clear()

        # 4) Launch a brand‐new replay thread que llame `_run_replay`
        self.streaming_1m = True
        self._replay_thread = threading.Thread(target=self._run_replay, daemon=True)
        self._replay_thread.start()

    def pause(self):
        """
        Detiene el streaming sin limpiar el buffer ni modificar from_time.
        Al pausar, simplemente señalamos al hilo que pare; las barras 
        parciales en _1m_buffer se conservan para reanudar agrupamiento.
        """
        print(f"[DEBUG PAUSE] Pausando sin vaciar buffer: buffer_size={len(self._1m_buffer)}  current_group_start={self._current_group_start}")
        
        # Señalar al hilo que debe detenerse
        self._stop_replay.set()
        self.streaming_1m = False

        # Esperar a que el hilo termine
        if self._replay_thread and self._replay_thread.is_alive():
            self._replay_thread.join()
        
        print(f"[DEBUG PAUSE] Hilo detenido, current_1m_index={self.current_1m_index}")

    def _run_replay(self):
        """
        Bucle interno en un thread aparte:
        Lee raw_1m desde current_1m_index; para cada barra:
          - SL/TP callback
          - Si TF='1m', emite inmediatamente
          - Sino, la agrega en buffer alineada al clock window. Al detectarse
            cambio de window, agrupa+emite TF bar y arranca buffer nuevo.
        Duerme 0.1s entre barras, chequeando pausa cada 0.01s.
        """
        n = len(self.raw_1m)
        i = self.current_1m_index
        print(f"[DEBUG RUN] iniciando _run_replay con current_1m_index={self.current_1m_index}")

        while i < n:
            print(f"[DEBUG RUN] iteración: i={i}, current_1m_index={self.current_1m_index}")
            if self._stop_replay.is_set():
                print(f"[DEBUG RUN] detected pause flag, saliendo de _run_replay")
                return

            bar = self.raw_1m[i]
            ts  = bar['time']

            print(f"[DEBUG RUN] procesando raw_1m[{i}] → ts={ts} → next index será {i + 1}")
            self.current_1m_index = i + 1

            self._process_bar(bar)

            i += 1

        print(f"[DEBUG RUN] all raw_1m bars processed, terminando _run_replay")

    def _handle_message(self, msg: dict):
        """
        (No usado en este ejemplo, ya que `_run_replay` llama a `_process_bar/tick` directamente.)
        """
        if not self.streaming_1m:
            return

        ts = msg.get('time', 0)
        if ts <= self._from_time:
            return

        if 'open' in msg and 'high' in msg:
            self._process_bar(msg)
        elif 'price' in msg:
            self._process_tick(msg)

    def _process_bar(self, bar1: dict):
        """
        Handle a single 1m bar:
          1) SL/TP callback on raw 1m.
          2) Si current_tf == '1m', emite inmediatamente.
          3) Sino, la agrega a un buffer alineado por “clock window” de N minutos:
             - window_secs = group_size * 60
             - window_start = floor(bar['time'] / window_secs) * window_secs
             - Si coincide con current_group_start, append a buffer.
             - Si cambia, agrupa y emite la TF bar anterior, luego limpia buffer,
               y arranca la nueva ventana.
        """
        ts = bar1['time']
        window_secs = self.group_size * 60
        window_start = (ts // window_secs) * window_secs

        # **Nueva línea para que imprimas el current_tf que realmente estás usando:**
        print(f"[DEBUG] nueva barra 1m: ts={ts}  current_tf={self.current_tf}  window_secs={window_secs}  window_start={window_start}  current_group_start={self._current_group_start}")

        # 1) SL/TP check
        if self.bar_callback:
            self.bar_callback(bar1)

        # 2) Si TF == '1m', emite directamente:
        if self.current_tf.endswith('m') and int(self.current_tf[:-1]) == 1:
            self.socketio.emit('bar', bar1)
            time.sleep(0.1)
            return

        # 3) Si es TF > 1m, agrupamos en buffer
        with self.stream_lock:
            if self._current_group_start is None:
                # Primera barra en la ventana actual
                self._current_group_start = window_start

            if window_start == self._current_group_start:
                # Sigue en la misma ventana
                self._1m_buffer.append(bar1)
                print(f"[DEBUG] → buffer mismo window ({window_start}): size={len(self._1m_buffer)}")
            else:
                # Cambia el “clock window”: emitimos la ventana anterior
                if self._1m_buffer:
                    print(f"[DEBUG] ventana completada: group_start={self._current_group_start}  bars_in_buffer={len(self._1m_buffer)}  próximo window_start={window_start}")
                    tf_bar = self._aggregate_time_window(self._1m_buffer, self._current_group_start, window_secs)
                    print(f"[DEBUG] → emitiendo TF‐bar: time={tf_bar['time']}  open={tf_bar['open']}  high={tf_bar['high']}  low={tf_bar['low']}  close={tf_bar['close']}  volume={tf_bar['volume']}")

                    if self.strategy:
                        try:
                            self.strategy.on_new_bar(tf_bar)
                        except Exception:
                            import traceback; traceback.print_exc()
                    self.socketio.emit('bar', tf_bar)
                    time.sleep(0.1)

                # Limpiar buffer y arrancar la nueva ventana
                self._1m_buffer = [bar1]
                self._current_group_start = window_start

    def _process_tick(self, tick: dict):
        """
        Process a live tick (no grouping), check SL/TP, and emit 'tick'.
        """
        if self.bar_callback:
            self.bar_callback(tick)
        self.socketio.emit('tick', tick)

    def _aggregate_time_window(self, bars: list, window_start: int, window_secs: int) -> dict:
        """
        Aggregate all 1m bars within a single clock‐aligned window:
          - window_start: UNIX epoch for the first second of the window.
          - window_secs: total length of window in seconds.
        Returns a single dict with:
          time  = window_start + window_secs  (timestamp aligned to end of window),
          open  = first bar's open,
          high  = max(high),
          low   = min(low),
          close = last bar's close,
          volume= sum(volume),
          pair  = bars[0]['pair'].
        """
        high    = max(b['high']  for b in bars)
        low     = min(b['low']   for b in bars)
        open_   = bars[0]['open']
        close_  = bars[-1]['close']
        volume  = sum(b['volume'] for b in bars)
        pair    = bars[0]['pair']
        aligned_time = window_start + window_secs

        return {
            'time':   aligned_time,
            'open':   open_,
            'high':   high,
            'low':    low,
            'close':  close_,
            'volume': volume,
            'pair':   pair
        }

    def prepare_agg_bars(self, tf: str, start_time: int = None) -> list:
        """
        The REST GET /api/bars endpoint:
         - If tf == '1m', return a slice of raw_1m between INITIAL_START..start_time.
         - Otherwise, group raw_1m into tf‐bars on the fly and slice by time window.
        """
        # 1m case: return raw slice
        if tf.endswith('m') and int(tf[:-1]) == 1:
            if start_time is None:
                start_ts = int(self.config.INITIAL_START.timestamp())
                end_ts   = int(self.config.INITIAL_END.timestamp())
            else:
                start_ts, end_ts = 0, start_time

            idx = next((i for i, b in enumerate(self.raw_1m) if b['time'] > end_ts),
                       len(self.raw_1m))
            self.current_1m_index = idx
            return [b for b in self.raw_1m if start_ts <= b['time'] <= end_ts]

        # Non‐1m: compute window_secs and group raw_1m directly by clock windows
        num, unit = int(tf[:-1]), tf[-1]
        if unit == 'h':
            window_secs = num * 3600
        else:  # unit == 'm'
            window_secs = num * 60

        if start_time is None:
            start_ts = int(self.config.INITIAL_START.timestamp())
            end_ts   = int(self.config.INITIAL_END.timestamp())
        else:
            start_ts, end_ts = 0, start_time

        sliced = [b for b in self.raw_1m if start_ts <= b['time'] <= end_ts]

        # Advance pointer for REST streaming sync
        idx = next((i for i, b in enumerate(self.raw_1m) if b['time'] > end_ts),
                   len(self.raw_1m))
        self.current_1m_index = idx

        # Group sliced 1m bars into TF bars by clock windows
        from collections import defaultdict
        buckets = defaultdict(list)
        for b in sliced:
            win = (b['time'] // window_secs) * window_secs
            buckets[win].append(b)

        agg_bars = []
        for win_start in sorted(buckets.keys()):
            bars_in_win = buckets[win_start]
            if not bars_in_win:
                continue
            agg_bars.append(self._aggregate_time_window(
                bars_in_win,
                win_start,
                window_secs
            ))

        return agg_bars