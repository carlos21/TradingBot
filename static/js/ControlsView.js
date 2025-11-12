export class ControlsView {
  constructor(chartViewer, socket, dataService) {
    this.chartViewer = chartViewer;
    this.socket      = socket;
    this.dataService = dataService;

    this.currentPrice    = chartViewer.lastPrice;
    this.stopLossPoints  = 15;
    this.tradeSide       = 'buy';
    this.activeTrade     = null;

    // UI elements
    this.decreaseStopLossButton = null;
    this.increaseStopLossButton = null;
    this.stopLossLabel          = null;
    this.buyButton              = null;
    this.sellButton             = null;
    this.openButton             = null;
    this.closeButton            = null;
    this.toggleBtn              = null;
    this.tfButtons              = [];
  }

  init() {
    // grab DOM elements
    this.decreaseStopLossButton = document.getElementById('decrease-stop-loss');
    this.increaseStopLossButton = document.getElementById('increase-stop-loss');
    this.stopLossLabel          = document.getElementById('stop-loss-value');
    this.buyButton              = document.getElementById('buy-button');
    this.sellButton             = document.getElementById('sell-button');
    this.openButton             = document.getElementById('open-trade-button');
    this.closeButton            = document.getElementById('close-trade-button');
    this.toggleBtn              = document.getElementById('toggleReplayBtn');
    this.prevBtn = document.getElementById('prevDayBtn');
    this.nextBtn = document.getElementById('nextDayBtn');
    this.currentDayLabel   = document.getElementById('currentDayLabel');

    this.tfButtons              = Array.from(document.querySelectorAll('[data-timeframe]'));

    this.stopLossLabel.innerText = this.stopLossPoints;
    this.updateCloseButtonState();

    // bind all handlers
    this.bindEvents();
    this.bindTradeEvents();
    this.bindSocketEvents();
    this.bindReplayEvents();
    this.bindTimeframeEvents();

    // initial draw
    this.updateCurrentPrice(this.chartViewer.lastPrice);

    // default timeframe button
    const defaultBtn = document.querySelector('[data-timeframe="5m"]');
    if (defaultBtn) this.setActiveTf(defaultBtn);
  }

  bindEvents() {
    this.decreaseStopLossButton.addEventListener('click', () => this.changeStopLossPoints(-1));
    this.increaseStopLossButton.addEventListener('click', () => this.changeStopLossPoints(1));
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
    this.openButton.addEventListener('click', () => this.handleOpenTrade());
    this.closeButton.addEventListener('click', () => this.handleCloseTrade());
  }

  bindSocketEvents() {
    this.socket.on('trade_open', trade => {
      this.activeTrade = trade;
      this.updateCloseButtonState();
      this.chartViewer._drawTradeLines(trade);
      this.chartViewer.clearPreviews();
    });
    this.socket.on('trade_close', () => {
      this.activeTrade = null;
      this.updateCloseButtonState();
    });
    this.socket.on('bar', bar => {
      if (!this.activeTrade) {
        this.updateCurrentPrice(bar.close);
      }
    });
  }

  bindReplayEvents() {
    this.toggleBtn.addEventListener('click', () => {
      this.chartViewer.toggleReplay();
      this.toggleBtn.textContent = this.chartViewer.isPlaying ? 'Pause' : 'Play';
    });

    this.prevBtn.addEventListener('click', () => {
      this.chartViewer.jumpToDay(-1);
    });

    this.nextBtn.addEventListener('click', () => {
      this.chartViewer.jumpToDay(1);
    });
  }

  bindTimeframeEvents() {
    this.tfButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        this.setActiveTf(btn);
        const tf = btn.getAttribute('data-timeframe');
        this.chartViewer.changeTimeframe(tf);
      });
    });
  }

  setActiveTf(button) {
    this.tfButtons.forEach(b => b.classList.remove('active'));
    button.classList.add('active');
  }

  updateCloseButtonState() {
    this.closeButton.disabled = !this.activeTrade;
  }

  async handleOpenTrade() {
    try {
      await this.dataService.openTrade(
        this.chartViewer.pair,
        this.tradeSide,
        this.stopLossPrice
      );
    } catch (err) {
      console.error('Open trade failed:', err);
    }
  }

  async handleCloseTrade() {
    if (!this.activeTrade) return;
    try {
      await this.dataService.closeTrade(this.activeTrade.trade_id);
    } catch (err) {
      console.error('Close trade failed:', err);
    }
  }

  changeStopLossPoints(delta) {
    this.stopLossPoints += delta;
    this.recalculatePrices();
    this.updateView();
  }

  updateCurrentPrice(newPrice) {
    this.currentPrice = newPrice;
    this.recalculatePrices();
    this.updateView();
  }

  recalculatePrices() {
    if (this.tradeSide === 'buy') {
      this.stopLossPrice = this.currentPrice - this.stopLossPoints;
      this.takeProfit    = this.currentPrice + this.stopLossPoints * 4;
    } else {
      this.stopLossPrice = this.currentPrice + this.stopLossPoints;
      this.takeProfit    = this.currentPrice - this.stopLossPoints * 4;
    }
  }

  updateView() {
    this.stopLossLabel.innerText = this.stopLossPoints;
    // this.chartViewer._drawPreviews({
    //   entry:       this.currentPrice,
    //   stop_loss:   this.stopLossPrice,
    //   take_profit: this.takeProfit
    // });
  }

  updateTradeButtons() {
    if (this.tradeSide === 'buy') {
      this.buyButton.classList.add('bg-green-500');
      this.buyButton.classList.remove('bg-gray-500');
      this.sellButton.classList.add('bg-gray-500');
      this.sellButton.classList.remove('bg-red-500');
    } else {
      this.sellButton.classList.add('bg-red-500');
      this.sellButton.classList.remove('bg-gray-500');
      this.buyButton.classList.add('bg-gray-500');
      this.buyButton.classList.remove('bg-green-500');
    }
  }
}
