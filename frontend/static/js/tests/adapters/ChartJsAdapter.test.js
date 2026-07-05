import { describe, it, expect, vi } from 'vitest';
import { ChartJsAdapter } from '../../adapters/charts/ChartJsAdapter.js';

describe('ChartJsAdapter', () => {
  function makeLibrary() {
    return vi.fn((ctx, options) => ({
      ctx,
      options,
      data: null,
      update: vi.fn(),
      destroy: vi.fn(),
    }));
  }

  it('creates a chart from a canvas element', () => {
    const Library = makeLibrary();
    const adapter = new ChartJsAdapter(Library);
    const ctx = { fake: true };
    const canvas = document.createElement('canvas');
    canvas.getContext = vi.fn(() => ctx);
    const chart = adapter.createChart(canvas, { type: 'line' });

    expect(canvas.getContext).toHaveBeenCalledWith('2d');
    expect(Library).toHaveBeenCalledWith(ctx, { type: 'line' });
    expect(chart.ctx).toBe(ctx);
  });

  it('creates a chart from a context object', () => {
    const Library = makeLibrary();
    const adapter = new ChartJsAdapter(Library);
    const ctx = { fake: true };
    adapter.createChart(ctx, { type: 'bar' });
    expect(Library).toHaveBeenCalledWith(ctx, { type: 'bar' });
  });

  it('sets data and updates', () => {
    const adapter = new ChartJsAdapter(makeLibrary());
    const chart = adapter.createChart(document.createElement('canvas'), {});
    const data = { labels: ['a'], datasets: [] };
    adapter.setData(chart, data);
    expect(chart.data).toBe(data);
    expect(chart.update).toHaveBeenCalled();
  });

  it('updates data and refreshes', () => {
    const adapter = new ChartJsAdapter(makeLibrary());
    const chart = adapter.createChart(document.createElement('canvas'), {});
    const data = { labels: ['b'], datasets: [] };
    adapter.update(chart, data);
    expect(chart.data).toBe(data);
    expect(chart.update).toHaveBeenCalled();
  });

  it('destroys a chart and removes it from instances', () => {
    const adapter = new ChartJsAdapter(makeLibrary());
    const chart = adapter.createChart(document.createElement('canvas'), {});
    adapter.destroy(chart);
    expect(chart.destroy).toHaveBeenCalled();
  });

  it('destroys all charts', () => {
    const adapter = new ChartJsAdapter(makeLibrary());
    const c1 = adapter.createChart(document.createElement('canvas'), {});
    const c2 = adapter.createChart(document.createElement('canvas'), {});
    adapter.destroyAll();
    expect(c1.destroy).toHaveBeenCalled();
    expect(c2.destroy).toHaveBeenCalled();
  });
});
