import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AnalyticsCharts } from '../../admin/AnalyticsCharts.js';

function mockChart() {
  const instances = [];
  const ChartMock = vi.fn().mockImplementation((ctx, config) => {
    const instance = {
      ctx,
      config,
      destroy: vi.fn(),
      _metasets: [{
        total: config.data.datasets[0].data.reduce((a, b) => a + b, 0),
      }],
    };
    instances.push(instance);
    return instance;
  });
  return { ChartMock, instances };
}

describe('AnalyticsCharts', () => {
  let chartMock;
  let charts;

  beforeEach(() => {
    chartMock = mockChart();
    global.Chart = chartMock.ChartMock;
    document.body.innerHTML = `
      <canvas id="overview-equity-chart"></canvas>
      <canvas id="overview-result-chart"></canvas>
      <canvas id="analytics-hour-chart"></canvas>
      <canvas id="analytics-day-chart"></canvas>
      <canvas id="analytics-monthly-chart"></canvas>
      <canvas id="analytics-pnl-dist-chart"></canvas>
    `;
    document.querySelectorAll('canvas').forEach(canvas => {
      canvas.getContext = vi.fn().mockReturnValue({});
    });
    charts = new AnalyticsCharts();
  });

  afterEach(() => {
    delete global.Chart;
  });

  it('destroyAll destroys all chart instances', () => {
    charts.renderEquityCurve('overview-equity-chart', { labels: ['Jan'], data: [100] });
    charts.renderResultDistribution('overview-result-chart', { labels: ['TP'], data: [1] });

    const destroyed = chartMock.instances.map(i => i.destroy);
    charts.destroyAll();

    expect(destroyed[0]).toHaveBeenCalled();
    expect(destroyed[1]).toHaveBeenCalled();
    expect(Object.keys(charts.charts).length).toBe(0);
  });

  it('renderEquityCurve creates a line chart with month labels', () => {
    const data = {
      labels: ['Jan', '', '', 'Feb'],
      data: [100, 110, 120, 130],
    };
    charts.renderEquityCurve('overview-equity-chart', data);

    const instance = chartMock.instances[0];
    expect(instance.config.type).toBe('line');
    expect(instance.config.data.datasets[0].data).toEqual(data.data);

    const tooltipTitle = instance.config.options.plugins.tooltip.callbacks.title([{ dataIndex: 3 }]);
    expect(tooltipTitle).toBe('Feb');
  });

  it('renderResultDistribution creates a doughnut chart with consistent colors', () => {
    const data = { labels: ['TP', 'SL', 'BE', 'SP', 'X'], data: [5, 3, 1, 2, 1] };
    charts.renderResultDistribution('overview-result-chart', data);

    const instance = chartMock.instances[0];
    expect(instance.config.type).toBe('doughnut');
    const colors = instance.config.data.datasets[0].backgroundColor;
    expect(colors[0]).toBe(charts.colors.success);
    expect(colors[1]).toBe(charts.colors.danger);
    expect(colors[2]).toBe(charts.colors.warning);
    expect(colors[3]).toBe(charts.colors.primary);
    expect(colors[4]).toBe(charts.colors.gray);

    const label = instance.config.options.plugins.tooltip.callbacks.label({
      label: 'TP',
      raw: 5,
      datasetIndex: 0,
      chart: instance,
    });
    expect(label).toContain('TP: 5');
  });

  it('renderTradesByHour creates a bar chart', () => {
    charts.renderTradesByHour('analytics-hour-chart', { labels: ['09'], data: [3] });
    const instance = chartMock.instances[0];
    expect(instance.config.type).toBe('bar');
    expect(instance.config.data.datasets[0].label).toBe('Number of Trades');
  });

  it('renderTradesByDay creates a bar chart', () => {
    charts.renderTradesByDay('analytics-day-chart', { labels: ['Mon'], data: [2] });
    const instance = chartMock.instances[0];
    expect(instance.config.type).toBe('bar');
  });

  it('renderMonthlyPnl creates a bar chart with win/loss colors', () => {
    charts.renderMonthlyPnl('analytics-monthly-chart', { labels: ['Jan', 'Feb'], data: [100, -50] });
    const instance = chartMock.instances[0];
    expect(instance.config.type).toBe('bar');
    expect(instance.config.data.datasets[0].backgroundColor).toEqual([
      charts.colors.success,
      charts.colors.danger,
    ]);
  });

  it('renderPnlDistribution creates a histogram', () => {
    charts.renderPnlDistribution('analytics-pnl-dist-chart', { labels: ['0-100'], data: [4] });
    const instance = chartMock.instances[0];
    expect(instance.config.type).toBe('bar');
    expect(instance.config.options.scales.x.title.text).toBe('P&L Range');
  });

  it('subsequent renders destroy previous chart', () => {
    charts.renderEquityCurve('overview-equity-chart', { labels: ['Jan'], data: [100] });
    const first = chartMock.instances[0];
    charts.renderEquityCurve('overview-equity-chart', { labels: ['Feb'], data: [200] });

    expect(first.destroy).toHaveBeenCalled();
    expect(chartMock.ChartMock).toHaveBeenCalledTimes(2);
  });
});
