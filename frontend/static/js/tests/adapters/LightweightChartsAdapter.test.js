import { describe, it, expect, vi } from 'vitest';
import { LightweightChartsAdapter } from '../../adapters/charts/LightweightChartsAdapter.js';

function makeFakeSeries(id) {
  const line = { options: { price: 0 } };
  return {
    id,
    createPriceLine: vi.fn((opts) => {
      line.options = opts;
      return line;
    }),
    removePriceLine: vi.fn(),
    setData: vi.fn(),
    update: vi.fn(),
    setMarkers: vi.fn(),
    applyOptions: vi.fn(),
    applyLineOptions: vi.fn(),
    options: vi.fn(() => ({ price: 100 })),
    coordinateToPrice: vi.fn((c) => c * 2),
  };
}

function makeFakeLibrary() {
  return {
    createChart: vi.fn((container, options) => {
      const series = [];
      const chart = {
        container,
        options,
        series,
        remove: vi.fn(),
        resize: vi.fn(),
        priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
        timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
        subscribeClick: vi.fn(),
        addCandlestickSeries: vi.fn((opts) => {
          const s = makeFakeSeries('candlestick');
          s.options = vi.fn(() => opts);
          series.push(s);
          return s;
        }),
        addLineSeries: vi.fn((opts) => {
          const s = makeFakeSeries('line');
          s.options = vi.fn(() => opts);
          series.push(s);
          return s;
        }),
        addHistogramSeries: vi.fn((opts) => {
          const s = makeFakeSeries('histogram');
          series.push(s);
          return s;
        }),
      };
      return chart;
    }),
  };
}

describe('LightweightChartsAdapter', () => {
  it('creates a chart', () => {
    const lib = makeFakeLibrary();
    const adapter = new LightweightChartsAdapter(lib);
    const container = document.createElement('div');
    const chart = adapter.createChart(container, { width: 100 });

    expect(lib.createChart).toHaveBeenCalledWith(container, { width: 100 });
    expect(chart.container).toBe(container);
  });

  it('adds series and price lines', () => {
    const adapter = new LightweightChartsAdapter(makeFakeLibrary());
    const chart = adapter.createChart(document.createElement('div'), {});

    const candle = adapter.addCandlestickSeries(chart, { color: 'red' });
    const line = adapter.addLineSeries(chart, { color: 'blue' });
    const hist = adapter.addHistogramSeries(chart, { color: 'green' });
    const priceLine = adapter.createPriceLine(line, { price: 50 });

    expect(chart.addCandlestickSeries).toHaveBeenCalledWith({ color: 'red' });
    expect(chart.addLineSeries).toHaveBeenCalledWith({ color: 'blue' });
    expect(chart.addHistogramSeries).toHaveBeenCalledWith({ color: 'green' });
    expect(line.createPriceLine).toHaveBeenCalledWith({ price: 50 });
    expect(candle.id).toBe('candlestick');
    expect(priceLine.options.price).toBe(50);
  });

  it('sets and updates data and markers', () => {
    const adapter = new LightweightChartsAdapter(makeFakeLibrary());
    const chart = adapter.createChart(document.createElement('div'), {});
    const series = chart.addLineSeries({});
    adapter.setData(series, [1, 2]);
    adapter.update(series, { time: 1, value: 2 });
    adapter.setMarkers(series, [{ x: 1 }]);
    adapter.applyOptions(series, { width: 2 });

    expect(series.setData).toHaveBeenCalledWith([1, 2]);
    expect(series.update).toHaveBeenCalledWith({ time: 1, value: 2 });
    expect(series.setMarkers).toHaveBeenCalledWith([{ x: 1 }]);
    expect(series.applyOptions).toHaveBeenCalledWith({ width: 2 });
  });

  it('applies price scale options', () => {
    const adapter = new LightweightChartsAdapter(makeFakeLibrary());
    const chart = adapter.createChart(document.createElement('div'), {});
    adapter.applyPriceScaleOptions(chart, 'right', { visible: true });
    expect(chart.priceScale).toHaveBeenCalledWith('right');
  });

  it('applies line options and reads price line options', () => {
    const adapter = new LightweightChartsAdapter(makeFakeLibrary());
    const line = { options: vi.fn(() => ({ price: 99 })), applyOptions: vi.fn() };
    adapter.applyLineOptions(line, { color: 'red' });
    expect(line.applyOptions).toHaveBeenCalledWith({ color: 'red' });
    expect(adapter.priceLineOptions(line).price).toBe(99);
    expect(line.options).toHaveBeenCalled();
  });

  it('resizes, fits content, and subscribes to clicks', () => {
    const adapter = new LightweightChartsAdapter(makeFakeLibrary());
    const chart = adapter.createChart(document.createElement('div'), {});
    const handler = () => {};

    adapter.resize(chart, 100, 200);
    expect(chart.resize).toHaveBeenCalledWith(100, 200);

    adapter.fitContent(chart);
    expect(chart.timeScale).toHaveBeenCalled();

    adapter.subscribeClick(chart, handler);
    expect(chart.subscribeClick).toHaveBeenCalledWith(handler);
  });

  it('coordinates to price', () => {
    const adapter = new LightweightChartsAdapter(makeFakeLibrary());
    const chart = adapter.createChart(document.createElement('div'), {});
    const series = chart.addLineSeries({});
    expect(adapter.coordinateToPrice(series, 10)).toBe(20);
  });

  it('destroys the chart', () => {
    const adapter = new LightweightChartsAdapter(makeFakeLibrary());
    const chart = adapter.createChart(document.createElement('div'), {});
    adapter.destroy(chart);
    expect(chart.remove).toHaveBeenCalled();
  });
});
