// (path: static/js/main.js)
import { ChartViewer } from './ChartViewer.js';
import { DataService } from './DataService.js';
import { ControlsView } from './ControlsView.js';

document.addEventListener("DOMContentLoaded", () => {
    const chartContainer = document.getElementById('chartContainer');
    const socket = io();
    const service = new DataService();
    
    // Parse start_time, keep_lines, AND tf from URL query params
    const urlParams = new URLSearchParams(window.location.search);
    const startTimeParam = urlParams.get('start_time');
    const startTime = startTimeParam ? parseInt(startTimeParam, 10) : null;
    const keepLines = urlParams.get('keep_lines') === 'true';
    const tfParam = urlParams.get('tf'); // [CHANGE] Get tf param

    const chartViewer = new ChartViewer(chartContainer, service, socket, {
        startTime: startTime,
        keepStrategyLines: keepLines,
        timeframe: tfParam // [CHANGE] Pass to ChartViewer
    });
    window.chartViewer = chartViewer;
    const controlsView = new ControlsView(chartViewer, socket, service);
    controlsView.init();

    chartViewer.onDisplay = () => controlsView.updateView();
});