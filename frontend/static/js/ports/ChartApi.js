/**
 * Abstract chart API port.
 * Wraps lightweight-charts (or any other charting library) so the
 * application layer does not depend on the concrete library.
 *
 * Every method operates on opaque handles returned by factory methods.
 */
export class IChartApi {
  createChart(container, options) { throw new Error('IChartApi.createChart not implemented'); }

  addCandlestickSeries(chart, options) { throw new Error('IChartApi.addCandlestickSeries not implemented'); }
  addLineSeries(chart, options) { throw new Error('IChartApi.addLineSeries not implemented'); }
  addHistogramSeries(chart, options) { throw new Error('IChartApi.addHistogramSeries not implemented'); }

  createPriceLine(series, options) { throw new Error('IChartApi.createPriceLine not implemented'); }
  removePriceLine(series, line) { throw new Error('IChartApi.removePriceLine not implemented'); }
  applyLineOptions(line, options) { throw new Error('IChartApi.applyLineOptions not implemented'); }
  priceLineOptions(line) { throw new Error('IChartApi.priceLineOptions not implemented'); }

  setData(series, data) { throw new Error('IChartApi.setData not implemented'); }
  update(series, data) { throw new Error('IChartApi.update not implemented'); }
  setMarkers(series, markers) { throw new Error('IChartApi.setMarkers not implemented'); }
  applyOptions(series, options) { throw new Error('IChartApi.applyOptions not implemented'); }
  applyPriceScaleOptions(chart, scaleId, options) { throw new Error('IChartApi.applyPriceScaleOptions not implemented'); }

  resize(chart, width, height) { throw new Error('IChartApi.resize not implemented'); }
  timeScale(chart) { throw new Error('IChartApi.timeScale not implemented'); }
  fitContent(chart) { throw new Error('IChartApi.fitContent not implemented'); }
  subscribeClick(chart, handler) { throw new Error('IChartApi.subscribeClick not implemented'); }
  coordinateToPrice(series, coordinate) { throw new Error('IChartApi.coordinateToPrice not implemented'); }

  destroy(chart) { throw new Error('IChartApi.destroy not implemented'); }
}
