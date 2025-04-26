export class ControlsView {
  constructor(chartViewer, config = {}) {
    this.chartViewer = chartViewer;
    this.currentPrice = config.currentPrice || 112.69;
    this.stopLossPoints = config.stopLossPoints || 15;
    this.tradeSide = config.tradeSide || 'buy';
    this.recalculatePrices();
  }

  recalculatePrices() {
    if (this.tradeSide === 'buy') {
      // For BUY trades, stop loss is below the entry and take profit above.
      this.stopLossPrice = this.currentPrice - this.stopLossPoints;
      this.takeProfit = this.currentPrice + this.stopLossPoints * 4;
    } else if (this.tradeSide === 'sell') {
      // For SELL trades, stop loss is above the entry and take profit below.
      this.stopLossPrice = this.currentPrice + this.stopLossPoints;
      this.takeProfit = this.currentPrice - this.stopLossPoints * 4;
    }
  }

  init() {
    // Get DOM elements for stop loss controls
    this.decreaseStopLossButton = document.getElementById('decrease-stop-loss');
    this.increaseStopLossButton = document.getElementById('increase-stop-loss');
    this.stopLossLabel = document.getElementById('stop-loss-value');
    // Get DOM elements for trade action buttons
    this.buyButton = document.getElementById('buy-button');
    this.sellButton = document.getElementById('sell-button');

    // Initialize the label with the current stop loss price (formatted)
    this.stopLossLabel.innerText = this.stopLossPoints;

    this.bindEvents();
    this.bindTradeEvents();
    this.updateTradeButtons();
  }

  bindEvents() {
    this.decreaseStopLossButton.addEventListener('click', () => {
      this.changeStopLossPoints(-1);
    });
    this.increaseStopLossButton.addEventListener('click', () => {
      this.changeStopLossPoints(1);
    });
  }

  bindTradeEvents() {
    this.buyButton.addEventListener('click', () => {
      this.tradeSide = 'buy';
      this.recalculatePrices();
      this.updateView();
      this.updateTradeButtons();
    });
    this.sellButton.addEventListener('click', () => {
      this.tradeSide = 'sell';
      this.recalculatePrices();
      this.updateView();
      this.updateTradeButtons();
    });
  }

  changeStopLossPoints(delta) {
    this.stopLossPoints += delta;
    this.recalculatePrices();
    this.updateView();
  }

  updateCurrentPrice(newPrice) {
    // Called externally when a new bar arrives
    this.currentPrice = newPrice;
    this.recalculatePrices();
    this.updateView();
  }

  updateView() {
    // Update the stop loss label with the current stop loss price
    this.stopLossLabel.innerText = this.stopLossPoints;
    // Update the chart lines via the chart viewer
    this.chartViewer.updatePriceLines(
      this.currentPrice,
      this.stopLossPrice,
      this.takeProfit
    );
  }

  updateTradeButtons() {
    if (this.tradeSide === 'buy') {
      // If BUY is selected, show green background on BUY and gray on SELL.
      this.buyButton.classList.remove('bg-gray-500');
      this.buyButton.classList.add('bg-green-500');
      this.sellButton.classList.remove('bg-red-500');
      this.sellButton.classList.add('bg-gray-500');
    } else {
      // If SELL is selected, show red background on SELL and gray on BUY.
      this.sellButton.classList.remove('bg-gray-500');
      this.sellButton.classList.add('bg-red-500');
      this.buyButton.classList.remove('bg-green-500');
      this.buyButton.classList.add('bg-gray-500');
    }
  }
}