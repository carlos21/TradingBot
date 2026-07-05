import { describe, it, expect, vi } from 'vitest';
import { calculateTSI, detectCrosses } from '../TSICalculator.js';

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

  it('logs crossovers to console', () => {
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    const tsiRaw = [-1, 1];
    const signalRaw = [0, 0];
    detectCrosses(tsiRaw, signalRaw, [3, 4]);

    expect(logSpy).toHaveBeenCalled();
    expect(logSpy.mock.calls[0][0]).toContain('[TSI_CROSS]');
    expect(logSpy.mock.calls[0][0]).toContain('dir=bullish');
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