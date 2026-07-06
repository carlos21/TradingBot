/**
 * Pure marker-building helpers for the chart.
 * No DOM or chart-library dependencies.
 */

export const MARKER_SHAPES = {
  ARROW_UP: 'arrowUp',
  ARROW_DOWN: 'arrowDown',
};

export const MARKER_POSITIONS = {
  BELOW_BAR: 'belowBar',
  ABOVE_BAR: 'aboveBar',
};

export const COLORS = {
  ENTRY: '#2962FF',
  WIN: '#00E676',
  LOSS: '#FF1744',
  BULL: '#00E676',
  BEAR: '#FF1744',
};

/**
 * Build an entry marker descriptor for a trade.
 * Returns null if the entry time is not yet visible.
 */
export function buildEntryMarker(trade, lastTime) {
  const entryTime = trade.entry_time ?? trade.entryTime;
  if (!entryTime || entryTime > lastTime) return null;

  const isLong = trade.type === 'long';
  const regime = trade.velocity_regime || '';
  let text = regime ? `Entry · ${regime}` : 'Entry';
  if (trade.close_on_opposite_cross) {
    text = 'Entry → OppCross';
  }

  return {
    time: entryTime,
    position: isLong ? MARKER_POSITIONS.BELOW_BAR : MARKER_POSITIONS.ABOVE_BAR,
    shape: isLong ? MARKER_SHAPES.ARROW_UP : MARKER_SHAPES.ARROW_DOWN,
    color: COLORS.ENTRY,
    text,
    size: 1,
    key: `entry_${trade.trade_id}`,
  };
}

/**
 * Build an exit marker descriptor for a closed trade.
 * Returns null if the trade is open or the exit time is not yet visible.
 */
export function buildExitMarker(trade, lastTime) {
  if (trade.status !== 'closed') return null;
  const exitTime = trade.exit_time ?? trade.exitTime;
  if (!exitTime || exitTime > lastTime) return null;

  const isLong = trade.type === 'long';
  const res = trade.result || 0;

  return {
    time: exitTime,
    position: isLong ? MARKER_POSITIONS.ABOVE_BAR : MARKER_POSITIONS.BELOW_BAR,
    shape: isLong ? MARKER_SHAPES.ARROW_DOWN : MARKER_SHAPES.ARROW_UP,
    color: res > 0 ? COLORS.WIN : COLORS.LOSS,
    text: `${res > 0 ? '+' : ''}${res.toFixed(2)}R`,
    size: 2,
    key: `exit_${trade.trade_id}`,
  };
}

/**
 * Build all trade markers that should be visible at the current replay time.
 */
export function buildTradeMarkers(trades, lastTime) {
  if (!isFinite(lastTime)) return [];

  const markers = [];
  for (const trade of trades) {
    const entry = buildEntryMarker(trade, lastTime);
    if (entry) markers.push(entry);
    const exit = buildExitMarker(trade, lastTime);
    if (exit) markers.push(exit);
  }
  return markers;
}

/**
 * Deduplicate markers by (time, text/shape), preserving order.
 */
export function deduplicateMarkers(markers) {
  markers.sort((a, b) => a.time - b.time);
  const seen = new Set();
  const unique = [];
  for (const m of markers) {
    const k = `${m.time}_${m.text || m.shape}`;
    if (!seen.has(k)) {
      seen.add(k);
      unique.push(m);
    }
  }
  return unique;
}

/**
 * Keep only markers whose time exists in the current bar set.
 * Bar times are integers; marker times may be floats with microsecond
 * precision, so floor them before checking membership.
 */
export function filterMarkersByValidTimes(markers, validTimes) {
  if (!validTimes || validTimes.size === 0) {
    return markers.filter(m => m.time != null);
  }
  return markers.filter(m => m.time != null && validTimes.has(Math.floor(m.time)));
}

/**
 * Combine TSI markers with trade markers, deduplicate, and filter to
 * safe bar times.
 */
export function composeMarkers(tsiMarkers, trades, lastTime, validTimes) {
  const tradeMarkers = buildTradeMarkers(trades, lastTime);
  const combined = [...(tsiMarkers || []), ...tradeMarkers];
  const deduped = deduplicateMarkers(combined);
  return filterMarkersByValidTimes(deduped, validTimes);
}
