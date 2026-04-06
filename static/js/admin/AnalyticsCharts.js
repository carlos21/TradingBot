/**
 * Analytics Charts Component
 * Manages all Chart.js instances for the dashboard
 */
export class AnalyticsCharts {
  constructor() {
    this.charts = {};
    this.colors = {
      primary: '#3b82f6',
      success: '#10b981',
      danger: '#ef4444',
      warning: '#f59e0b',
      purple: '#8b5cf6',
      gray: '#6b7280',
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
              color: '#9ca3af', 
              maxTicksLimit: 8,
              callback: function(val, index) {
                // Only show month labels (non-empty ones)
                const label = this.getLabelForValue(val);
                return label && label.trim() !== '' ? label : '';
              }
            },
            grid: { color: '#374151' },
          },
          y: {
            ticks: { color: '#9ca3af' },
            grid: { color: '#374151' },
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
    // TP = green (success), SL = red (danger), BE = yellow (warning), SP = blue
    const resultColors = {
      'TP': this.colors.success,   // TP - Green
      'SL': this.colors.danger,    // SL - Red
      'BE': '#f59e0b',             // BE - Amber/Yellow
      'SP': '#3b82f6',             // SP - Blue
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
            labels: { color: '#9ca3af' },
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
            ticks: { color: '#9ca3af' },
            grid: { display: false },
          },
          y: {
            ticks: { color: '#9ca3af' },
            grid: { color: '#374151' },
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
            ticks: { color: '#9ca3af' },
            grid: { display: false },
          },
          y: {
            ticks: { color: '#9ca3af' },
            grid: { color: '#374151' },
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
            ticks: { color: '#9ca3af', maxTicksLimit: 12 },
            grid: { display: false },
          },
          y: {
            ticks: { color: '#9ca3af' },
            grid: { color: '#374151' },
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
            ticks: { color: '#9ca3af' },
            grid: { display: false },
            title: {
              display: true,
              text: 'P&L Range',
              color: '#9ca3af',
            },
          },
          y: {
            ticks: { color: '#9ca3af' },
            grid: { color: '#374151' },
          },
        },
      },
    });
  }
}
