import { createChartApp } from './composition/chartApp.js';
import { NtAccountsDisplay } from './NtAccountsDisplay.js';
import { StreamHealthPanel } from './StreamHealthPanel.js';
import { StreamingEventType, StreamingState } from './domain/streamingLifecycle.js';
import { resolvePinnedPair, findInstrument } from './domain/instruments.js';
import { InstrumentsMenuController } from './application/InstrumentsMenuController.js';
import { OverlayInstrumentController } from './application/OverlayInstrumentController.js';
import { InstrumentSwitchController } from './application/InstrumentSwitchController.js';
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
    // This tab is pinned to one instrument only via a valid ?pair= param.
    // Without one nothing is pinned: no room is joined, no bars load, and
    // the overlay selector demands an explicit choice.
    activePair = resolvePinnedPair(getUrlPair(), instruments);
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

  const { controller, socket, streamingLifecycle, streamingControls } = createChartApp({
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

  // "Ready to Trade" overlay instrument picker: shown whenever the tab is
  // unpinned (no valid ?pair=) or several instruments exist. Picking an
  // instrument only updates the Start button — the tab switches to it in
  // place (no reload) when Start Streaming is clicked (see beforeStart below).
  const overlaySelector = new OverlayInstrumentController(new BrowserDomService());
  overlaySelector.init();
  overlaySelector.setInstruments(config?.instruments || [], activePair);

  overlaySelector.onSelectionChange = (symbol) => {
    streamingLifecycle.setStartSymbol(symbol);
    const startBtn = document.getElementById('startStreamingBtn');
    if (startBtn) startBtn.disabled = false;
    setConnectionStatus(`Ready to stream ${symbol}`);
  };

  // Without a pinned instrument there is nothing to stream yet: keep Start
  // disabled until the user picks an instrument in the overlay selector.
  if (!activePair) {
    const startBtn = document.getElementById('startStreamingBtn');
    if (startBtn) startBtn.disabled = true;
    setConnectionStatus('Select an instrument to stream');
  }

  function joinActiveInstrument() {
    if (activePair) socket.emit('join_instrument', { pair: activePair });
  }
  socket.on('connect', joinActiveInstrument);
  joinActiveInstrument();

  window.addEventListener('beforeunload', () => {
    if (activePair) socket.emit('leave_instrument', { pair: activePair });
  });

  const accountsDisplay = new NtAccountsDisplay(socket, activePair);
  accountsDisplay.init();

  const healthPanel = new StreamHealthPanel(socket, activePair);
  window.healthPanel = healthPanel;

  const instrumentSwitcher = new InstrumentSwitchController({
    socket,
    chartController: controller,
    lifecycle: streamingLifecycle,
    healthPanel,
    accountsDisplay,
    dom: new BrowserDomService(),
  });

  // If the picker points at an instrument other than the one this tab is
  // pinned to, Start Streaming first switches the tab to it in place (room,
  // chart data, labels, URL), then proceeds with the normal start flow — the
  // state machine stays in STARTING (busy) until the platform connects.
  streamingControls.beforeStart = () => {
    const selected = overlaySelector.getSelectedSymbol();
    if (selected && selected !== activePair) {
      const instrument = findInstrument(config?.instruments || [], selected);
      instrumentSwitcher.switchTo(selected, instrument, activePair);
      activePair = selected;
      activeInstrument = instrument;
    }
    return true;
  };

  // The stream health panel only makes sense while the platform is actually
  // streaming — drive its visibility from the streaming lifecycle machine.
  healthPanel.setActive(streamingLifecycle.getState() === StreamingState.STREAMING);
  streamingLifecycle.onStateChange = (state) => {
    healthPanel.setActive(state === StreamingState.STREAMING);
    // Don't allow changing the instrument only while a start is in flight;
    // IDLE/GATEWAY_UP/DISCONNECTED all still let the user pick and switch.
    overlaySelector.setEnabled(state !== StreamingState.STARTING);
  };

  // On page load, sync the streaming lifecycle machine with the backend so
  // the overlay and streaming controls match the real gateway state.
  fetch('/api/stream/status')
    .then(r => r.json())
    .then(data => {
      streamingLifecycle.dispatch({ type: StreamingEventType.STATUS_SYNC, ...data });
      // The dispatch re-renders the start button; re-apply the unpinned gate
      // unless the user has already picked an instrument.
      if (!activePair) {
        if (!overlaySelector.getSelectedSymbol()) {
          const startBtn = document.getElementById('startStreamingBtn');
          if (startBtn) startBtn.disabled = true;
          setConnectionStatus('Select an instrument to stream');
        }
        return;
      }
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
