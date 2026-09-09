import { describe, it, expect, vi } from 'vitest';
import { calculateTSI, detectCrosses, IncrementalTSI } from '../TSICalculator.js';

function buildBars(count) {
  const bars = [];
  for (let i = 0; i < count; i++) {
    bars.push({
      time: 1700000000 + i * 60,
      open: 100 + i,
      high: 101 + i,
      low: 99 + i,
      close: 100 + i + (i % 2 === 0 ? 0.5 : -0.25),
    });
  }
  return bars;
}

// Deterministic pseudo-random walk for equivalence tests.
function buildRandomBars(count) {
  let state = 42;
  const rand = () => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return state / 2147483648;
  };
  const bars = [];
  let price = 20000;
  for (let i = 0; i < count; i++) {
    price += (rand() - 0.5) * 20;
    bars.push({
      time: 1700000000 + i * 300,
      open: price,
      high: price + 5,
      low: price - 5,
      close: price,
      volume: 10,
    });
  }
  return bars;
}

describe('TSICalculator', () => {
  it('returns empty arrays for null or short input', () => {
    expect(calculateTSI(null)).toEqual({ tsiData: [], signalData: [], tsiRaw: [], signalRaw: [] });
    expect(calculateTSI([])).toEqual({ tsiData: [], signalData: [], tsiRaw: [], signalRaw: [] });
    expect(calculateTSI(buildBars(13))).toEqual({ tsiData: [], signalData: [], tsiRaw: [], signalRaw: [] });
  });

  it('calculates TSI and signal arrays for sufficient bars', () => {
    const bars = buildBars(50);
    const result = calculateTSI(bars);

    expect(result.tsiData.length).toBe(bars.length);
    expect(result.signalData.length).toBe(bars.length);
    expect(result.tsiRaw.length).toBe(bars.length);
    expect(result.signalRaw.length).toBe(bars.length);

    expect(result.tsiData[0]).toEqual({ time: bars[0].time, value: 0 });
    expect(result.signalData[0]).toEqual({ time: bars[0].time, value: 0 });
  });

  it('handles zero emaAbsPc values by setting TSI to 0', () => {
    const bars = Array.from({ length: 20 }, (_, i) => ({
      time: 1700000000 + i * 60,
      open: 100,
      high: 100,
      low: 100,
      close: 100,
    }));
    const result = calculateTSI(bars);
    expect(result.tsiRaw.every(v => v === 0)).toBe(true);
  });

  it('detects bullish crossover', () => {
    const tsiRaw = [-1, 1];
    const signalRaw = [0, 0];
    const times = [3, 4];
    const markers = detectCrosses(tsiRaw, signalRaw, times);

    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({
      time: 4,
      position: 'belowBar',
      color: '#00E676',
      shape: 'arrowUp',
      size: 1,
    });
  });

  it('detects bearish crossover', () => {
    const tsiRaw = [1, -1];
    const signalRaw = [0, 0];
    const times = [3, 4];
    const markers = detectCrosses(tsiRaw, signalRaw, times);

    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({
      time: 4,
      position: 'aboveBar',
      color: '#FF1744',
      shape: 'arrowDown',
      size: 1,
    });
  });

  it('does not log crossovers to console', () => {
    // detectCrosses runs on a full rescan per bar; logging every cross each
    // call floods the console (and the automation driver) on long replays.
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    const tsiRaw = [-1, 1];
    const signalRaw = [0, 0];
    detectCrosses(tsiRaw, signalRaw, [3, 4]);

    expect(logSpy).not.toHaveBeenCalled();
    logSpy.mockRestore();
  });

  it('returns no markers when there is no crossover', () => {
    const tsiRaw = [0, 0, 0, 0];
    const signalRaw = [0, 0, 0, 0];
    const markers = detectCrosses(tsiRaw, signalRaw, [1, 2, 3, 4]);
    expect(markers).toEqual([]);
  });

  it('falls back to zero TSI when result is not finite', () => {
    const padded = [
      ...Array.from({ length: 14 }, (_, i) => ({ time: i, open: 0, high: 0, low: 0, close: 0 })),
      { time: 15, open: 0, high: 0, low: 0, close: Number.POSITIVE_INFINITY },
    ];
    const result = calculateTSI(padded);
    const finiteTsi = result.tsiRaw.filter(Number.isFinite);
    expect(finiteTsi.length).toBe(result.tsiRaw.length);
  });
});
describe('IncrementalTSI', () => {
  it('rejects seeding with too few bars and updates before seeding', () => {
    const inc = new IncrementalTSI();
    expect(inc.isSeeded()).toBe(false);
    expect(inc.update({ time: 1, close: 100 })).toBeNull();
    expect(inc.seed(buildBars(13))).toBe(false);
    expect(inc.isSeeded()).toBe(false);
  });

  it('matches calculateTSI/detectCrosses exactly when seeded then extended', () => {
    const bars = buildRandomBars(500);
    const seedCount = 100;
    const inc = new IncrementalTSI();
    expect(inc.seed(bars.slice(0, seedCount))).toBe(true);

    const tsiValues = [];
    const sigValues = [];
    const crosses = [];
    for (let i = seedCount; i < bars.length; i++) {
      const r = inc.update(bars[i]);
      expect(r).not.toBeNull();
      expect(r.amended).toBe(false);
      tsiValues.push(r.tsiPoint.value);
      sigValues.push(r.sigPoint.value);
      expect(r.tsiPoint.time).toBe(bars[i].time);
      if (r.cross) crosses.push(r.cross);
    }

    const full = calculateTSI(bars);
    const times = bars.map(b => b.time);
    expect(tsiValues).toEqual(full.tsiRaw.slice(seedCount));
    expect(sigValues).toEqual(full.signalRaw.slice(seedCount));

    const allCrosses = detectCrosses(full.tsiRaw, full.signalRaw, times);
    expect(crosses).toEqual(allCrosses.filter(m => m.time >= bars[seedCount].time));
  });

  it('amending the last bar matches a full recalculation, repeatedly', () => {
    const bars = buildRandomBars(120);
    const inc = new IncrementalTSI();
    inc.seed(bars.slice(0, 100));
    for (let i = 100; i < bars.length; i++) inc.update(bars[i]);

    for (const delta of [8, -3, 0.75]) {
      const amendedBar = { ...bars[119], close: bars[119].close + delta };
      const r = inc.update(amendedBar);
      expect(r).not.toBeNull();
      expect(r.amended).toBe(true);

      const full = calculateTSI([...bars.slice(0, 119), amendedBar]);
      const fullCross = crossAtLastPair(full, [...bars.slice(0, 119), amendedBar]);
      expect(r.tsiPoint.value).toBe(full.tsiRaw[119]);
      expect(r.sigPoint.value).toBe(full.signalRaw[119]);
      expect(r.cross).toEqual(fullCross);
    }
  });

  it('returns null for bars older than the last applied bar', () => {
    const bars = buildRandomBars(30);
    const inc = new IncrementalTSI();
    inc.seed(bars);
    expect(inc.update({ time: bars[10].time, close: 1 })).toBeNull();
  });
});

function crossAtLastPair(full, bars) {
  const n = full.tsiRaw.length;
  const markers = detectCrosses(
    full.tsiRaw.slice(n - 2),
    full.signalRaw.slice(n - 2),
    bars.map(b => b.time).slice(n - 2)
  );
  return markers.length > 0 ? markers[0] : null;
}
