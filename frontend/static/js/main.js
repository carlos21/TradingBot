import { createChartApp } from './composition/chartApp.js';
import { NtAccountsDisplay } from './NtAccountsDisplay.js';
import { StreamHealthPanel } from './StreamHealthPanel.js';

function hideOverlay() {
  const overlay = document.getElementById('connectionOverlay');
  if (overlay) overlay.classList.add('hidden');
}

function showOverlay() {
  const overlay = document.getElementById('connectionOverlay');
  if (overlay) overlay.classList.remove('hidden');
}

function setConnectionStatus(text) {
  const el = document.getElementById('connectionStatus');
  if (el) el.textContent = text;
}

function setStopStreamingVisible(visible) {
  const btn = document.getElementById('stopStreamingBtn');
  if (btn) btn.classList.toggle('hidden', !visible);
}

function getUrlPair() {
  const params = new URLSearchParams(window.location.search);
  return params.get('pair');
}

function setUrlPair(pair) {
  const url = new URL(window.location.href);
  url.searchParams.set('pair', pair);
  history.replaceState({}, '', url);
}

function populateSelect(select, instruments, selectedSymbol) {
  if (!select) return;
  select.innerHTML = '';
  for (const inst of instruments) {
    const opt = document.createElement('option');
    opt.value = inst.symbol;
    opt.textContent = `${inst.symbol} — ${inst.full_name}`;
    select.appendChild(opt);
  }
  select.value = selectedSymbol;
}

function updateStartButtonText(symbol) {
  const btn = document.getElementById('startStreamingBtn');
  if (!btn) return;
  const span = btn.querySelector('span');
  if (span) span.textContent = `Start Streaming ${symbol || ''}`.trim();
}

async function loadConfig() {
  const res = await fetch('/api/config');
  if (!res.ok) throw new Error('config fetch failed');
  return res.json();
}

function resolveActiveInstrument(instruments, urlPair) {
  if (urlPair) {
    const found = instruments.find(i => i.symbol === urlPair);
    if (found) return found;
  }
  return instruments[0] || null;
}

document.addEventListener('DOMContentLoaded', async () => {
  const chartContainer = document.getElementById('chartContainer');
  const headerSelect = document.getElementById('instrumentSelector');
  const overlaySelect = document.getElementById('overlayInstrumentSelector');

  let config;
  let activeInstrument = null;
  let activePair = null;

  try {
    config = await loadConfig();
    const instruments = Array.isArray(config.instruments) ? config.instruments : [];
    activeInstrument = resolveActiveInstrument(instruments, getUrlPair());
    activePair = activeInstrument ? activeInstrument.symbol : (config.pair || null);

    populateSelect(headerSelect, instruments, activePair);
    populateSelect(overlaySelect, instruments, activePair);
    updateStartButtonText(activePair);
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

  const { controller, socket } = createChartApp({
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

  window.chartViewer = controller;

  function joinActiveInstrument() {
    if (activePair) socket.emit('join_instrument', { pair: activePair });
  }
  socket.on('connect', joinActiveInstrument);
  joinActiveInstrument();

  function onInstrumentChange(symbol) {
    const instrument = config?.instruments?.find(i => i.symbol === symbol);
    if (!instrument || !controller) return;

    const oldPair = activePair;
    activePair = instrument.symbol;
    activeInstrument = instrument;
    setUrlPair(activePair);

    if (headerSelect) headerSelect.value = activePair;
    if (overlaySelect) overlaySelect.value = activePair;
    updateStartButtonText(activePair);

    if (oldPair && oldPair !== activePair) {
      socket.emit('leave_instrument', { pair: oldPair });
    }
    socket.emit('join_instrument', { pair: activePair });

    controller.setActiveInstrument(activePair, activeInstrument);
  }

  headerSelect?.addEventListener('change', (e) => onInstrumentChange(e.target.value));
  overlaySelect?.addEventListener('change', (e) => onInstrumentChange(e.target.value));

  window.addEventListener('beforeunload', () => {
    if (activePair) socket.emit('leave_instrument', { pair: activePair });
  });

  const accountsDisplay = new NtAccountsDisplay(socket);
  accountsDisplay.init();

  const healthPanel = new StreamHealthPanel(socket);
  window.healthPanel = healthPanel;

  // On page load, immediately check if we're already connected.
  fetch('/api/stream/status')
    .then(r => r.json())
    .then(data => {
      if (data.live_mode) setStopStreamingVisible(!!data.gateway_running);
      if (data.platform_connected) {
        hideOverlay();
        setConnectionStatus('Connected');
        return;
      }
      if (data.live_mode && data.has_accounts === false) {
        showOverlay();
        setConnectionStatus('⚠️ No accounts configured. Go to Admin → Settings to add one.');
        return;
      }
      if (data.live_mode && !data.gateway_running) {
        showOverlay();
        setConnectionStatus('Waiting for platform connection...');
        return;
      }
      if (data.gateway_running) {
        showOverlay();
        setConnectionStatus('Waiting for platform connection...');
      }
    })
    .catch(() => {
      // If the API call fails, let Socket.IO handle it.
    });
});
