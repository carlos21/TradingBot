import { ChartViewer } from './ChartViewer.js';
import { DataService } from './DataService.js';
import { ControlsView } from './ControlsView.js';
import { NtAccountsDisplay } from './NtAccountsDisplay.js';

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

document.addEventListener("DOMContentLoaded", () => {
    const chartContainer = document.getElementById('chartContainer');
    const socket = io();
    const service = new DataService();
    
    // Parse URL query params
    const urlParams = new URLSearchParams(window.location.search);
    const startTimeParam = urlParams.get('start_time');
    const startTime = startTimeParam ? parseInt(startTimeParam, 10) : null;
    
    // keep_lines = Strategy Lines (Blue Dotted)
    const keepStrategyLines = urlParams.get('keep_lines') === 'true';
    
    // keep_closed_trades = Trade Lines (Entry/SL/TP) after close
    const keepClosedTrades = urlParams.get('keep_closed_trades') === 'true';
    
    const tfParam = urlParams.get('tf');
    const showTSI = urlParams.get('show_tsi') === 'true';

    const chartViewer = new ChartViewer(chartContainer, service, socket, {
        startTime: startTime,
        keepStrategyLines: keepStrategyLines,
        keepClosedTradeLines: keepClosedTrades,
        timeframe: tfParam,
        showTSI: showTSI
    });
    window.chartViewer = chartViewer;
    const controlsView = new ControlsView(chartViewer, socket);
    controlsView.init();

    const accountsDisplay = new NtAccountsDisplay(socket);
    accountsDisplay.init();

    // On page load, immediately check if we're already connected.
    // This prevents the overlay from flashing when refreshing while
    // NinjaTrader is already connected.
    fetch('/api/stream/status')
        .then(r => r.json())
        .then(data => {
            if (data.platform_connected) {
                hideOverlay();
                setConnectionStatus('Connected');
                return;
            }
            if (data.live_mode && !data.gateway_running) {
                // Live mode but gateway not started — show overlay and wait for user to click Start Streaming
                showOverlay();
                setConnectionStatus('Waiting for NinjaTrader connection...');
                return;
            }
            if (data.gateway_running) {
                // Gateway is bound but NT hasn't connected yet
                showOverlay();
                setConnectionStatus('Waiting for NinjaTrader connection...');
            }
        })
        .catch(() => {
            // If the API call fails, let Socket.IO handle it
        });
});
