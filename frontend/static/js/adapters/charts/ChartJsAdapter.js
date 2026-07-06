import { IChartApi } from '../../ports/ChartApi.js';

/**
 * Adapter for Chart.js used by the admin analytics charts.
 */
export class ChartJsAdapter extends IChartApi {
  constructor(library) {
    super();
    this.library = library;
    this.instances = new Map();
  }

  createChart(container, options) {
    // container is expected to be a canvas element context or the canvas itself
    const ctx = container.getContext ? container.getContext('2d') : container;
    const chart = new this.library(ctx, options);
    this.instances.set(chart, chart);
    return chart;
  }

  setData(chart, data) {
    chart.data = data;
    chart.update();
  }

  update(chart, data) {
    chart.data = data;
    chart.update();
  }

  destroy(chart) {
    chart.destroy();
    this.instances.delete(chart);
  }

  destroyAll() {
    for (const chart of this.instances.values()) {
      chart.destroy();
    }
    this.instances.clear();
  }
}
