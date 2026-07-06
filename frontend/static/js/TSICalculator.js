const TSI_LONG = 6;
const TSI_SHORT = 13;
const TSI_SIGNAL = 4;

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

export function detectCrosses(tsiRaw, signalRaw, times) {
  const markers = [];

  for (let i = 1; i < tsiRaw.length; i++) {
    const prevTsi = tsiRaw[i - 1];
    const currTsi = tsiRaw[i];
    const prevSig = signalRaw[i - 1];
    const currSig = signalRaw[i];

    if (prevTsi <= prevSig && currTsi > currSig) {
      const dt = new Date(times[i] * 1000).toISOString().slice(0, 19).replace('T', ' ');
      console.log('[TSI_CROSS] time=' + dt + ' tsi=' + currTsi.toFixed(2) + ' sig=' + currSig.toFixed(2) + ' dir=bullish');
      markers.push({
        time: times[i],
        position: 'belowBar',
        color: '#00E676',
        shape: 'arrowUp',
        size: 1,
      });
    } else if (prevTsi >= prevSig && currTsi < currSig) {
      const dt = new Date(times[i] * 1000).toISOString().slice(0, 19).replace('T', ' ');
      console.log('[TSI_CROSS] time=' + dt + ' tsi=' + currTsi.toFixed(2) + ' sig=' + currSig.toFixed(2) + ' dir=bearish');
      markers.push({
        time: times[i],
        position: 'aboveBar',
        color: '#FF1744',
        shape: 'arrowDown',
        size: 1,
      });
    }
  }

  return markers;
}
