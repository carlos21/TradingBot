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
        },
        scales: {
          x: {
            ticks: { color: '#9ca3af', maxTicksLimit: 8 },
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

    const colors = [this.colors.success, this.colors.danger, this.colors.warning, this.colors.gray];

    this.charts[canvasId] = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: data.labels,
        datasets: [{
          data: data.data,
          backgroundColor: colors,
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
