import { describe, it, expect } from 'vitest';
import {
  buildEntryMarker,
  buildExitMarker,
  buildTradeMarkers,
  deduplicateMarkers,
  filterMarkersByValidTimes,
  composeMarkers,
  MARKER_SHAPES,
  MARKER_POSITIONS,
  COLORS,
} from '../../domain/marker.js';

describe('marker domain', () => {
  const longTrade = {
    trade_id: 't1',
    type: 'long',
    entry: 100,
    entry_time: 1000,
    stop_loss: 99,
    take_profit: 102,
    status: 'open',
  };

  it('builds an entry marker for a visible long trade', () => {
    const m = buildEntryMarker(longTrade, 1100);
    expect(m).toEqual({
      time: 1000,
      position: MARKER_POSITIONS.BELOW_BAR,
      shape: MARKER_SHAPES.ARROW_UP,
      color: COLORS.ENTRY,
      text: 'Entry',
      size: 1,
      key: 'entry_t1',
    });
  });

  it('builds an entry marker with velocity regime', () => {
    const m = buildEntryMarker({ ...longTrade, velocity_regime: 'FAST' }, 1100);
    expect(m.text).toBe('Entry · FAST');
  });

  it('skips entry markers in the future', () => {
    expect(buildEntryMarker(longTrade, 900)).toBeNull();
  });

  it('builds an exit marker for a closed winning trade', () => {
    const trade = { ...longTrade, status: 'closed', exit_time: 1100, result: 1.5 };
    const m = buildExitMarker(trade, 1200);
    expect(m.position).toBe(MARKER_POSITIONS.ABOVE_BAR);
    expect(m.color).toBe(COLORS.WIN);
    expect(m.text).toBe('+1.50R');
  });

  it('builds an exit marker for a losing short trade', () => {
    const trade = {
      trade_id: 't2',
      type: 'short',
      entry: 100,
      entry_time: 1000,
      status: 'closed',
      exit_time: 1100,
      result: -0.8,
    };
    const m = buildExitMarker(trade, 1200);
    expect(m.shape).toBe(MARKER_SHAPES.ARROW_UP);
    expect(m.color).toBe(COLORS.LOSS);
    expect(m.text).toBe('-0.80R');
  });

  it('deduplicates markers by time and text/shape', () => {
    const markers = [
      { time: 1, text: 'A', shape: 'x' },
      { time: 1, text: 'A', shape: 'y' },
      { time: 2, text: 'B', shape: 'x' },
      { time: 1, text: 'C', shape: 'x' },
    ];
    const result = deduplicateMarkers(markers);
    expect(result).toHaveLength(3);
  });

  it('filters markers to valid bar times (flooring float timestamps)', () => {
    const markers = [
      { time: 1000.000123 },
      { time: 1001.5 },
      { time: 1002 },
      { time: 2000 },
    ];
    const valid = new Set([1000, 1001, 1002]);
    const result = filterMarkersByValidTimes(markers, valid);
    expect(result.map(r => r.time)).toEqual([1000.000123, 1001.5, 1002]);
  });

  it('composes TSI and trade markers', () => {
    const tsi = [{ time: 1000, shape: MARKER_SHAPES.ARROW_UP, text: '' }];
    const closed = { ...longTrade, status: 'closed', exit_time: 1100, result: 1 };
    const result = composeMarkers(tsi, [closed], 1200, new Set([1000, 1100]));
    expect(result).toHaveLength(3);
  });
});
