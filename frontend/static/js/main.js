import { createChartApp } from './composition/chartApp.js';
import { NtAccountsDisplay } from './NtAccountsDisplay.js';
import { StreamHealthPanel } from './StreamHealthPanel.js';
import { StreamingEventType } from './domain/streamingLifecycle.js';
import { resolvePinnedPair, findInstrument } from './domain/instruments.js';
import { InstrumentsMenuController } from './application/InstrumentsMenuController.js';
import { BrowserDomService } from './adapters/browser/BrowserDomService.js';

function setConnectionStatus(text) {
  const el = document.getElementById('connectionStatus');
  if (el) el.textContent = text;
}

function getUrlPair() {
  const params = new URLSearchParams(window.location.search);
  return params.get('pair');
}

async function loadConfig() {
  const res = await fetch('/api/config');
  if (!res.ok) throw new Error('config fetch failed');
  return res.json();
}

document.addEventListener('DOMContentLoaded', async () => {
  let config;
  let activeInstrument = null;
  let activePair = null;

  try {
    config = await loadConfig();
    const instruments = Array.isArray(config.instruments) ? config.instruments : [];
    // This tab is pinned to one instrument: ?pair= wins when it names a
    // known instrument, otherwise the instance default pair is used.
    activePair = resolvePinnedPair(getUrlPair(), instruments, config.pair);
    activeInstrument = findInstrument(instruments, activePair);
  } catch (err) {
    console.error('[main] Failed to load config:', err);
  }

  const urlParams = new URLSearchParams(window.location.search);
  const startTimeParam = urlParams.get('start_time');
  const startTime = startTimeParam ? parseInt(startTimeParam, 10) : null;
  const keepStrategyLines = urlParams.get('keep_lines') === 'true';
  const keepClosedTrades = urlParams.get('keep_closed_trades') === 'true';
  const tfParam = urlParams.get('tf') || '1m';
  const showTSIParam = urlParams.get('show_tsi');
  const showTSI = showTSIParam === null ? undefined : showTSIParam === 'true';

  const { controller, socket, streamingLifecycle } = createChartApp({
    options: {
      startTime,
      keepStrategyLines,
      keepClosedTradeLines: keepClosedTrades,
      timeframe: tfParam,
      showTSI,
      activePair,
      activeInstrument,
    },
  });
  streamingLifecycle.setStartSymbol(activePair);

  window.chartViewer = controller;

  // Static pinned-instrument labels (header + connection overlay).
  const headerLabel = document.getElementById('currentInstrumentLabel');
  if (headerLabel) headerLabel.textContent = activeInstrument?.full_name || activePair || '';
  const overlayLabel = document.getElementById('overlayInstrumentLabel');
  if (overlayLabel) {
    overlayLabel.textContent = activeInstrument
      ? `${activeInstrument.symbol} — ${activeInstrument.full_name}`
      : (activePair || '');
  }

  // Header "Instruments ▾" menu: each entry opens another instrument in a
  // new tab on this same backend instance.
  const instrumentsMenu = new InstrumentsMenuController(new BrowserDomService());
  instrumentsMenu.init();
  instrumentsMenu.setInstruments(config?.instruments || [], activePair);

  function joinActiveInstrument() {
    if (activePair) socket.emit('join_instrument', { pair: activePair });
  }
  socket.on('connect', joinActiveInstrument);
  joinActiveInstrument();

  window.addEventListener('beforeunload', () => {
    if (activePair) socket.emit('leave_instrument', { pair: activePair });
  });

  const accountsDisplay = new NtAccountsDisplay(socket);
  accountsDisplay.init();

  const healthPanel = new StreamHealthPanel(socket);
  window.healthPanel = healthPanel;

  // On page load, sync the streaming lifecycle machine with the backend so
  // the overlay and streaming controls match the real gateway state.
  fetch('/api/stream/status')
    .then(r => r.json())
    .then(data => {
      streamingLifecycle.dispatch({ type: StreamingEventType.STATUS_SYNC, ...data });
      if (data.platform_connected) {
        setConnectionStatus('Connected');
        return;
      }
      if (data.live_mode && data.has_accounts === false) {
        setConnectionStatus('⚠️ No accounts configured. Go to Admin → Settings to add one.');
        return;
      }
      if (data.live_mode) {
        setConnectionStatus('Waiting for platform connection...');
      }
    })
    .catch(() => {
      // If the API call fails, let Socket.IO handle it.
    });
});
