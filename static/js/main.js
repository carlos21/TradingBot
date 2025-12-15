import { ChartViewer } from './ChartViewer.js';
import { DataService } from './DataService.js';
import { ControlsView } from './ControlsView.js';

document.addEventListener("DOMContentLoaded", () => {
    const chartContainer = document.getElementById('chartContainer');
    const socket = io();
    const service = new DataService();
    const chartViewer = new ChartViewer(chartContainer, service, socket);
    window.chartViewer = chartViewer;
    const controlsView = new ControlsView(chartViewer, socket, service);
    controlsView.init();

    chartViewer.onDisplay = () => controlsView.updateView();
});