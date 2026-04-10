const COLORS = {
  ENTRY:   '#2962FF',
  WIN:     '#00E676',
  LOSS:    '#FF1744',
  BULL:    '#00E676',
  BEAR:    '#FF1744',
};

export class MarkerManager {
  constructor(series) {
    this.series = series;
    this.tsiMarkers = [];
  }

  setTSIMarkers(markers) {
    this.tsiMarkers = markers;
  }

  appendTSIMarker(crossType, time) {
    const marker = { time, size: 1 };
    if (crossType === 'bullish') {
      marker.position = 'belowBar';
      marker.color = COLORS.BULL;
      marker.shape = 'arrowUp';
    } else if (crossType === 'bearish') {
      marker.position = 'aboveBar';
      marker.color = COLORS.BEAR;
      marker.shape = 'arrowDown';
    }
    this.tsiMarkers.push(marker);
  }

  update(allTrades, lastTime, validTimes = null) {
    if (lastTime === -Infinity) return;

    const markers = [...this.tsiMarkers];

    for (const t of allTrades) {
      const entryTime = t.entry_time || t.entryTime;
      const exitTime = t.exit_time || t.exitTime;
      const isLong = t.type === 'long' || t.type === 'buy';

      if (entryTime && entryTime <= lastTime) {
        const regime = t.velocity_regime || '';
        const entryText = regime ? `Entry \u00b7 ${regime}` : 'Entry';
        markers.push({
          time: entryTime,
          position: isLong ? 'belowBar' : 'aboveBar',
          shape: isLong ? 'arrowUp' : 'arrowDown',
          color: COLORS.ENTRY,
          text: entryText,
          size: 1,
        });
      }

      if (t.status === 'closed' && exitTime && exitTime <= lastTime) {
        const res = t.result || 0;
        markers.push({
          time: exitTime,
          position: isLong ? 'aboveBar' : 'belowBar',
          shape: isLong ? 'arrowDown' : 'arrowUp',
          color: res > 0 ? COLORS.WIN : COLORS.LOSS,
          text: (res > 0 ? '+' : '') + res.toFixed(2) + 'R',
          size: 2,
        });
      }
    }

    // Deduplicate and sort
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

    // Only emit markers whose time exists in the current series.
    // LightweightCharts throws "Value is null" when a marker references a
    // time that has no matching bar (e.g. a 5m cross marker after a TF switch).
    
    // Round marker times to integers to match bar times (bar times are integers,
    // but trade timestamps from the backend may be floats with microsecond precision)
    const safe = validTimes 
      ? unique.filter(m => m.time != null && validTimes.has(Math.floor(m.time)))
      : unique.filter(m => m.time != null);
    this.series.setMarkers(safe);
  }
}
