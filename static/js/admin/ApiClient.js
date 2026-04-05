/**
 * API Client for Admin Dashboard
 * Handles all HTTP requests to the backend
 */
export class ApiClient {
  constructor() {
    this.baseUrl = '';
    this.pair = null;
  }

  async init() {
    // Get the pair from the API
    const response = await this.get('/api/pair');
    this.pair = response.pair;
    return this.pair;
  }

  async get(url) {
    const response = await fetch(`${this.baseUrl}${url}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.json();
  }

  async post(url, data) {
    const response = await fetch(`${this.baseUrl}${url}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.json();
  }

  async put(url, data) {
    const response = await fetch(`${this.baseUrl}${url}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.json();
  }

  async delete(url) {
    const response = await fetch(`${this.baseUrl}${url}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.status === 204 ? null : response.json();
  }

  // Admin API Methods
  async getStats() {
    return this.get(`/api/admin/stats?pair=${this.pair}`);
  }

  async getTrades(limit = 50, offset = 0) {
    return this.get(`/api/admin/trades?pair=${this.pair}&limit=${limit}&offset=${offset}`);
  }

  async getTradeDetail(tradeId) {
    return this.get(`/api/admin/trades/${tradeId}`);
  }

  async getAnalytics() {
    return this.get(`/api/admin/analytics?pair=${this.pair}`);
  }

  async getLines() {
    return this.get(`/api/lines?pair=${this.pair}`);
  }

  async addLine(price) {
    return this.post('/api/lines', { pair: this.pair, price });
  }

  async updateLine(lineId, price) {
    return this.put(`/api/lines/${lineId}`, { price });
  }

  async deleteLine(lineId) {
    return this.delete(`/api/lines/${lineId}`);
  }
}
