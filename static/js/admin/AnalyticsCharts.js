/**
 * Analytics Charts Component
 * Manages all Chart.js instances for the dashboard
 */
export class AnalyticsCharts {
  constructor() {
    this.charts = {};
    this.colors = {
      primary: this._cssVar('--accent-500', '#06b6d4'),
      success: '#34d399',
      danger: '#f43f5e',
      warning: '#fbbf24',
      purple: '#8b5cf6',
      gray: '#64748b',
    };
  }

  _cssVar(name, fallback) {
    if (typeof document === 'undefined') return fallback;
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  _chartColors() {
    return {
      tick: this._cssVar('--slate-400', '#94a3b8'),
      grid: this._cssVar('--slate-700', '#334155'),
    };
  }

  destroyAll() {
    Object.values(this.charts).forEach(chart => chart.destroy());
    this.charts = {};
  }

  // Overview: Equity Curve
  renderEquityCurve(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    if (this.charts[canvasId]) {
      this.charts[canvasId].destroy();
    }

    // Filter to only show labels at month boundaries (non-empty labels)
    // and create a filtered set of labels for display
    const monthLabels = [];
    const monthIndices = [];
    data.labels.forEach((label, idx) => {
      if (label && label.trim() !== '') {
        monthLabels.push(label);
        monthIndices.push(idx);
      }
    });

    this.charts[canvasId] = new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.labels,
        datasets: [{
          label: 'Cumulative P&L',
          data: data.data,
          borderColor: this.colors.primary,
          backgroundColor: `${this.colors.primary}20`,
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHoverRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                // Show the actual month label for this data point
                const idx = items[0].dataIndex;
                // Find nearest month label
                for (let i = idx; i >= 0; i--) {
                  if (data.labels[i] && data.labels[i].trim() !== '') {
                    return data.labels[i];
                  }
                }
                return '';
              }
            }
          }
        },
        scales: {
          x: {
            ticks: {
              color: this._chartColors().tick,
              maxTicksLimit: 8,
              callback: function(val, index) {
                // Only show month labels (non-empty ones)
                const label = this.getLabelForValue(val);
                return label && label.trim() !== '' ? label : '';
              }
            },
            grid: { color: this._chartColors().grid },
          },
          y: {
            ticks: { color: this._chartColors().tick },
            grid: { color: this._chartColors().grid },
          },
        },
      },
    });
  }

  // Overview: Result Distribution (Pie)
  renderResultDistribution(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    if (this.charts[canvasId]) {
      this.charts[canvasId].destroy();
    }

    // Define consistent colors for each result type
    // TP = emerald, SL = rose, BE = amber, SP = accent
    const resultColors = {
      'TP': this.colors.success,
      'SL': this.colors.danger,
      'BE': this.colors.warning,
      'SP': this.colors.primary,
    };

    // Map labels to consistent colors
    const backgroundColors = data.labels.map(label => resultColors[label] || this.colors.gray);

    this.charts[canvasId] = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: data.labels,
        datasets: [{
          data: data.data,
          backgroundColor: backgroundColors,
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'right',
            labels: { color: this._chartColors().tick },
          },
          tooltip: {
            callbacks: {
              label: function(context) {
                const label = context.label || '';
                const value = context.raw || 0;
                const total = context.chart._metasets[context.datasetIndex].total;
                const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                return `${label}: ${value} (${percentage}%)`;
              }
            }
          }
        },
      },
    });
  }

  // Analytics: Trades by Hour (Bar)
  renderTradesByHour(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    if (this.charts[canvasId]) {
      this.charts[canvasId].destroy();
    }

    this.charts[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: data.labels,
        datasets: [{
          label: 'Number of Trades',
          data: data.data,
          backgroundColor: this.colors.primary,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            ticks: { color: this._chartColors().tick },
            grid: { display: false },
          },
          y: {
            ticks: { color: this._chartColors().tick },
            grid: { color: this._chartColors().grid },
          },
        },
      },
    });
  }

  // Analytics: Trades by Day (Bar)
  renderTradesByDay(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    if (this.charts[canvasId]) {
      this.charts[canvasId].destroy();
    }

    this.charts[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: data.labels,
        datasets: [{
          label: 'Number of Trades',
          data: data.data,
          backgroundColor: this.colors.purple,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            ticks: { color: this._chartColors().tick },
            grid: { display: false },
          },
          y: {
            ticks: { color: this._chartColors().tick },
            grid: { color: this._chartColors().grid },
          },
        },
      },
    });
  }

  // Analytics: Monthly P&L (Bar)
  renderMonthlyPnl(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    if (this.charts[canvasId]) {
      this.charts[canvasId].destroy();
    }

    const colors = data.data.map(v => v >= 0 ? this.colors.success : this.colors.danger);

    this.charts[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: data.labels,
        datasets: [{
          label: 'P&L',
          data: data.data,
          backgroundColor: colors,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            ticks: { color: this._chartColors().tick, maxTicksLimit: 12 },
            grid: { display: false },
          },
          y: {
            ticks: { color: this._chartColors().tick },
            grid: { color: this._chartColors().grid },
          },
        },
      },
    });
  }

  // Analytics: P&L Distribution (Bar/Histogram)
  renderPnlDistribution(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    if (this.charts[canvasId]) {
      this.charts[canvasId].destroy();
    }

    this.charts[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: data.labels,
        datasets: [{
          label: 'Number of Trades',
          data: data.data,
          backgroundColor: this.colors.primary,
          borderRadius: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            ticks: { color: this._chartColors().tick },
            grid: { display: false },
            title: {
              display: true,
              text: 'P&L Range',
              color: this._chartColors().tick,
            },
          },
          y: {
            ticks: { color: this._chartColors().tick },
            grid: { color: this._chartColors().grid },
          },
        },
      },
    });
  }
}
