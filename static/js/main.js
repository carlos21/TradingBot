import { ChartViewer } from './ChartViewer.js';
import { DataService } from './DataService.js';
import { ControlsView } from './ControlsView.js';

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

    const chartViewer = new ChartViewer(chartContainer, service, socket, {
        startTime: startTime,
        keepStrategyLines: keepStrategyLines,
        keepClosedTradeLines: keepClosedTrades, // Pass this to constructor
        timeframe: tfParam
    });
    window.chartViewer = chartViewer;
    const controlsView = new ControlsView(chartViewer, socket, service);
    controlsView.init();

    chartViewer.onDisplay = () => controlsView.updateView();
});