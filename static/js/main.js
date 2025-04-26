import { ChartViewer } from './ChartViewer.js';
import { DataService } from './DataService.js';

document.addEventListener("DOMContentLoaded", () => {
    const chartContainer = document.getElementById('chartContainer');
    const service = new DataService();
    const viewer = new ChartViewer(chartContainer, service);

    // Setup Play/Pause toggle button
    const toggleBtn = document.getElementById('toggleReplayBtn');
    toggleBtn.addEventListener('click', () => {
        viewer.toggleReplay();
        toggleBtn.textContent = viewer.isPlaying ? 'Pause' : 'Play';
    });

    const tfButtons = Array.from(document.querySelectorAll('[data-timeframe]'));
    function setActiveTf(button) {
        tfButtons.forEach(b => b.classList.remove('active'));
        button.classList.add('active');
    }
    tfButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tf = btn.getAttribute('data-timeframe');
            setActiveTf(btn);
            viewer.changeTimeframe(tf);
        });
    });

    // initially pick 5m
    const defaultBtn = document.querySelector('[data-timeframe="5m"]');
    setActiveTf(defaultBtn);

});