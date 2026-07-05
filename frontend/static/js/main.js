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

document.addEventListener('DOMContentLoaded', () => {
  const chartContainer = document.getElementById('chartContainer');

  // Parse URL query params
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
    },
  });

  window.chartViewer = controller;

  const accountsDisplay = new NtAccountsDisplay(socket);
  accountsDisplay.init();

  const healthPanel = new StreamHealthPanel(socket);
  window.healthPanel = healthPanel;

  // On page load, immediately check if we're already connected.
  fetch('/api/stream/status')
    .then(r => r.json())
    .then(data => {
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
