import { IChartApi } from '../../ports/ChartApi.js';

/**
 * In-memory chart API that records every call.
 */
export class FakeChartApi extends IChartApi {
  constructor() {
    super();
    this.calls = [];
    this.seriesCounter = 0;
    this.lineCounter = 0;
    this.charts = new Map();
  }

  _log(method, args) {
    this.calls.push({ method, args: [...args] });
  }

  createChart(container, options) {
    this._log('createChart', [container, options]);
    const chart = { id: 'chart', container, options };
    this.charts.set(chart, []);
    return chart;
  }

  addCandlestickSeries(chart, options) {
    this._log('addCandlestickSeries', [chart, options]);
    const series = { id: `series-${++this.seriesCounter}`, type: 'candlestick', chart, options };
    this.charts.get(chart).push(series);
    return series;
  }

  addLineSeries(chart, options) {
    this._log('addLineSeries', [chart, options]);
    const series = { id: `series-${++this.seriesCounter}`, type: 'line', chart, options };
    this.charts.get(chart).push(series);
    return series;
  }

  addHistogramSeries(chart, options) {
    this._log('addHistogramSeries', [chart, options]);
    const series = { id: `series-${++this.seriesCounter}`, type: 'histogram', chart, options };
    this.charts.get(chart).push(series);
    return series;
  }

  createPriceLine(series, options) {
    this._log('createPriceLine', [series, options]);
    const line = { id: `line-${++this.lineCounter}`, series, options };
    return line;
  }

  removePriceLine(series, line) {
    this._log('removePriceLine', [series, line]);
  }

  setData(series, data) {
    this._log('setData', [series, data]);
  }

  update(series, data) {
    this._log('update', [series, data]);
  }

  setMarkers(series, markers) {
    this._log('setMarkers', [series, markers]);
  }

  applyOptions(series, options) {
    this._log('applyOptions', [series, options]);
  }

  applyPriceScaleOptions(chart, scaleId, options) {
    this._log('applyPriceScaleOptions', [chart, scaleId, options]);
  }

  applyLineOptions(line, options) {
    this._log('applyLineOptions', [line, options]);
  }

  priceLineOptions(line) {
    this._log('priceLineOptions', [line]);
    return line.options;
  }

  resize(chart, width, height) {
    this._log('resize', [chart, width, height]);
  }

  timeScale(chart) {
    this._log('timeScale', [chart]);
    return { fitContent: () => this._log('fitContent', [chart]) };
  }

  fitContent(chart) {
    this._log('fitContent', [chart]);
  }

  subscribeClick(chart, handler) {
    this._log('subscribeClick', [chart, handler]);
  }

  coordinateToPrice(series, coordinate) {
    this._log('coordinateToPrice', [series, coordinate]);
    return coordinate; // trivial mapping for tests
  }

  destroy(chart) {
    this._log('destroy', [chart]);
  }
}
