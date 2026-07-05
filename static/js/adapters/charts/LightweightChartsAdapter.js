import { IChartApi } from '../../ports/ChartApi.js';

/**
 * Adapter for the lightweight-charts library.
 *
 * All public methods accept opaque handles returned by other methods,
 * so callers never touch the underlying library objects directly.
 */
export class LightweightChartsAdapter extends IChartApi {
  constructor(library) {
    super();
    this.library = library;
  }

  createChart(container, options) {
    return this.library.createChart(container, options);
  }

  addCandlestickSeries(chart, options) {
    return chart.addCandlestickSeries(options);
  }

  addLineSeries(chart, options) {
    return chart.addLineSeries(options);
  }

  addHistogramSeries(chart, options) {
    return chart.addHistogramSeries(options);
  }

  createPriceLine(series, options) {
    return series.createPriceLine(options);
  }

  removePriceLine(series, line) {
    series.removePriceLine(line);
  }

  setData(series, data) {
    series.setData(data);
  }

  update(series, data) {
    series.update(data);
  }

  setMarkers(series, markers) {
    series.setMarkers(markers);
  }

  applyOptions(series, options) {
    series.applyOptions(options);
  }

  applyPriceScaleOptions(chart, scaleId, options) {
    chart.priceScale(scaleId).applyOptions(options);
  }

  applyLineOptions(line, options) {
    line.applyOptions(options);
  }

  priceLineOptions(line) {
    return line.options();
  }

  resize(chart, width, height) {
    chart.resize(width, height);
  }

  timeScale(chart) {
    return chart.timeScale();
  }

  fitContent(chart) {
    chart.timeScale().fitContent();
  }

  subscribeClick(chart, handler) {
    chart.subscribeClick(handler);
  }

  coordinateToPrice(series, coordinate) {
    return series.coordinateToPrice(coordinate);
  }

  destroy(chart) {
    chart.remove();
  }
}
