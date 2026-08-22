const TSI_LONG = 6;
const TSI_SHORT = 13;
const TSI_SIGNAL = 4;

const K_LONG = 2 / (TSI_LONG + 1);
const K_SHORT = 2 / (TSI_SHORT + 1);
const K_SIGNAL = 2 / (TSI_SIGNAL + 1);

function ema(values, length) {
  const k = 2 / (length + 1);
  const result = new Array(values.length).fill(0);
  result[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    result[i] = values[i] * k + result[i - 1] * (1 - k);
  }
  return result;
}

export function calculateTSI(bars) {
  if (!bars || bars.length < 14) {
    return { tsiData: [], signalData: [], tsiRaw: [], signalRaw: [] };
  }

  const closes = bars.map(b => b.close);
  const times = bars.map(b => b.time);

  const pc = [0];
  const absPc = [0];
  for (let i = 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    pc.push(diff);
    absPc.push(Math.abs(diff));
  }

  const emaPc = ema(ema(pc, TSI_LONG), TSI_SHORT);
  const emaAbsPc = ema(ema(absPc, TSI_LONG), TSI_SHORT);

  const tsiRaw = [];
  const tsiData = [];

  for (let i = 0; i < emaPc.length; i++) {
    let tsi = 0;
    if (emaAbsPc[i] !== 0) tsi = 100 * (emaPc[i] / emaAbsPc[i]);
    if (!isFinite(tsi)) tsi = 0;
    tsiRaw.push(tsi);
    tsiData.push({ time: times[i], value: tsi });
  }

  const signalRaw = ema(tsiRaw, TSI_SIGNAL);
  const signalData = signalRaw.map((val, i) => ({ time: times[i], value: val }));

  return { tsiData, signalData, tsiRaw, signalRaw };
}

function crossMarkerAt(prevTsi, prevSig, currTsi, currSig, time) {
  if (prevTsi <= prevSig && currTsi > currSig) {
    return { time, position: 'belowBar', color: '#00E676', shape: 'arrowUp', size: 1 };
  }
  if (prevTsi >= prevSig && currTsi < currSig) {
    return { time, position: 'aboveBar', color: '#FF1744', shape: 'arrowDown', size: 1 };
  }
  return null;
}

export function detectCrosses(tsiRaw, signalRaw, times) {
  const markers = [];

  for (let i = 1; i < tsiRaw.length; i++) {
    const marker = crossMarkerAt(tsiRaw[i - 1], signalRaw[i - 1], tsiRaw[i], signalRaw[i], times[i]);
    if (marker) markers.push(marker);
  }

  return markers;
}

/**
 * Incremental TSI calculator.
 *
 * Produces exactly the same values as a full calculateTSI()/detectCrosses()
 * pass, but advances one bar at a time in O(1) by carrying the EMA state.
 * Used on the per-bar streaming path so long replays don't pay an O(n^2)
 * full rescan for every bar.
 *
 * Seed it with the bars a full recalculation would have used (>= 14 bars),
 * then call update() for each new bar. Amending the most recent bar (same
 * timestamp, e.g. a still-forming candle) is supported: the last point is
 * recomputed from a checkpoint, exactly as a full rescan would.
 */
export class IncrementalTSI {
  constructor() {
    this.reset();
  }

  reset() {
    this.count = 0;
    this.lastTime = null;
    this._prevClose = 0;
    this._e1 = 0;
    this._e2 = 0;
    this._a1 = 0;
    this._a2 = 0;
    this._tsi = 0;
    this._sig = 0;
    this._checkpoint = null;
  }

  isSeeded() {
    return this.count > 0;
  }

  seed(bars) {
    this.reset();
    if (!bars || bars.length < 14) return false;
    this._prevClose = bars[0].close;
    this.lastTime = bars[0].time;
    this.count = 1;
    for (let i = 1; i < bars.length; i++) {
      this._advance(bars[i].close);
      this.lastTime = bars[i].time;
      this.count++;
    }
    this._checkpoint = null;
    return true;
  }

  /**
   * Advance one bar.
   * Returns null when the calculator is not seeded or the bar is older than
   * the last applied bar (caller should fall back to a full recalculation).
   * Otherwise returns { tsiPoint, sigPoint, cross, amended } where cross is a
   * marker object or null.
   */
  update(bar) {
    if (this.count === 0) return null;

    let amended = false;
    if (bar.time === this.lastTime) {
      if (!this._checkpoint) return null;
      this._restore(this._checkpoint);
      amended = true;
    } else if (bar.time > this.lastTime) {
      this._checkpoint = this._snapshot();
    } else {
      return null;
    }

    const prevTsi = this._tsi;
    const prevSig = this._sig;
    this._advance(bar.close);
    if (!amended) this.count++;
    this.lastTime = bar.time;

    return {
      tsiPoint: { time: bar.time, value: this._tsi },
      sigPoint: { time: bar.time, value: this._sig },
      cross: crossMarkerAt(prevTsi, prevSig, this._tsi, this._sig, bar.time),
      amended,
    };
  }

  _advance(close) {
    // Same recurrence and operation order as calculateTSI(), so values are
    // bit-for-bit identical to a full pass.
    const diff = close - this._prevClose;
    this._prevClose = close;
    this._e1 = diff * K_LONG + this._e1 * (1 - K_LONG);
    this._e2 = this._e1 * K_SHORT + this._e2 * (1 - K_SHORT);
    const absDiff = Math.abs(diff);
    this._a1 = absDiff * K_LONG + this._a1 * (1 - K_LONG);
    this._a2 = this._a1 * K_SHORT + this._a2 * (1 - K_SHORT);
    let tsi = 0;
    if (this._a2 !== 0) tsi = 100 * (this._e2 / this._a2);
    if (!isFinite(tsi)) tsi = 0;
    this._tsi = tsi;
    this._sig = tsi * K_SIGNAL + this._sig * (1 - K_SIGNAL);
  }

  _snapshot() {
    return {
      prevClose: this._prevClose,
      e1: this._e1,
      e2: this._e2,
      a1: this._a1,
      a2: this._a2,
      tsi: this._tsi,
      sig: this._sig,
      lastTime: this.lastTime,
      count: this.count,
    };
  }

  _restore(s) {
    this._prevClose = s.prevClose;
    this._e1 = s.e1;
    this._e2 = s.e2;
    this._a1 = s.a1;
    this._a2 = s.a2;
    this._tsi = s.tsi;
    this._sig = s.sig;
    this.lastTime = s.lastTime;
    this.count = s.count;
  }
}
