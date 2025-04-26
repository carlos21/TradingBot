export class ChartViewer {
    constructor(chartElement) {
      this.chartElement = chartElement;
      this.chartOptions = {
        layout: {
          background: { type: 'solid', color: 'white' },
          textColor: 'black',
        },
        grid: {
          vertLines: { color: '#e1e1e1' },
          horzLines: { color: '#e1e1e1' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        timeScale: { visible: false },
        width: chartElement.clientWidth,
        height: chartElement.clientHeight,
      };
  
      this.chart = LightweightCharts.createChart(chartElement, this.chartOptions);
      this.candlestickSeries = this.chart.addCandlestickSeries();
      this.candlestickSeries.setData([]);
  
      // Price line placeholders
      this.currentPriceLine = null;
      this.stopLossLine = null;
      this.takeProfitLine = null;

      this.userLine = null;
      // Subscribe to clicks on the chart
      this.chart.subscribeClick((param) => {
        if (!param.point) return;  // clicked outside plot area
        const { y } = param.point;
        // Convert pixel Y to price
        const price = this.chart.priceScale('right').coordinateToPrice(y);
        this._drawUserLine(price);
      });
    }

    _drawUserLine(price) {
      // If there's already a user line, just move it
      if (this.userLine) {
        this.userLine.applyOptions({ price });
        return;
      }
      // Otherwise, create it once
      this.userLine = this.candlestickSeries.createPriceLine({
        price,
        color: '#ff9900',
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Solid,
        axisLabelVisible: true,
        title: 'Pinned',
      });
    }
  
    createPriceLines(currentPrice, stopLossPrice, takeProfit) {
      // Create current price line (blue)
      this.currentPriceLine = this.candlestickSeries.createPriceLine({
        price: currentPrice,
        color: 'blue',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: 'Current Price',
      });
  
      // Create stop loss line (red)
      this.stopLossLine = this.candlestickSeries.createPriceLine({
        price: stopLossPrice,
        color: 'red',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: 'Stop Loss',
      });
  
      // Create take profit line (green)
      this.takeProfitLine = this.candlestickSeries.createPriceLine({
        price: takeProfit,
        color: 'green',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: 'Take Profit',
      });
    }
  
    updatePriceLines(currentPrice, stopLossPrice, takeProfit) {
      if (this.currentPriceLine) {
        this.currentPriceLine.applyOptions({ price: currentPrice });
      }
      if (this.stopLossLine) {
        this.stopLossLine.applyOptions({ price: stopLossPrice });
      }
      if (this.takeProfitLine) {
        this.takeProfitLine.applyOptions({ price: takeProfit });
      }
    }
  
    updateCandlestickData(bar) {
      this.candlestickSeries.update(bar);
    }
  
    resizeChart() {
      this.chart.resize(this.chartElement.clientWidth, this.chartElement.clientHeight);
    }
  }