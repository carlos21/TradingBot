import { ChartViewer } from './manual/ChartViewer.js';
import { DataFetcher } from './manual/DataFetcher.js';
import { WebSocketService } from './WebSocketService.js';
import { WatchListViewer } from './WatchListViewer.js';
import { ControlsView } from './manual/ControlsView.js';

document.addEventListener('DOMContentLoaded', () => {
  // Get DOM elements
  const chartElement = document.getElementById('chart');
  const watchlistElement = document.getElementById('watchlistItems');

  // Dependency injection: instantiate services with required dependencies.
  const chartViewer = new ChartViewer(chartElement);
  const dataFetcher = new DataFetcher('/api/data');
  const wsManager = new WebSocketService();
  const watchlistManager = new WatchListViewer(watchlistElement, '/api/symbols');

  // Instantiate ControlsView, injecting chartViewer and initial values.
  const controlsView = new ControlsView(chartViewer, {
    currentPrice: 112.69,
    stopLossPoints: 15,
  });
  controlsView.init();

  // Create initial price lines on the chart using the ControlsView state.
  chartViewer.createPriceLines(
    controlsView.currentPrice,
    controlsView.stopLossPrice,
    controlsView.takeProfit
  );

  // Setup websocket events
  wsManager.onConnect(() => {
    console.log('Connected to websocket server');
  });
  wsManager.onNewBar((bar) => {
    // Update candlestick series with the new bar
    chartViewer.updateCandlestickData(bar);

    // Update the centralized trading parameters via ControlsView
    controlsView.updateCurrentPrice(bar.close);
  });

  // Load the watchlist and handle symbol selection
  watchlistManager.loadWatchlist().then(() => {
    watchlistElement.addEventListener('watchlistSymbolSelected', (event) => {
      const symbol = event.detail;
      document.getElementById('ticker').value = symbol;
      // Fetch new data for the selected symbol
      // (Assuming timeframe, emaPeriod, and rsiPeriod are defined)
      dataFetcher.fetchData(symbol, timeframe, emaPeriod, rsiPeriod)
        .then(data => {
          chartViewer.candlestickSeries.setData(data.candlestick);
        })
        .catch(error => console.error('Error fetching data:', error));
    });
  });

  // Resize chart on window resize
  window.addEventListener('resize', () => {
    chartViewer.resizeChart();
  });
});