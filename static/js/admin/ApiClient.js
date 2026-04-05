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
    console.log('[ApiClient] Initializing, fetching pair...');
    const response = await this.get('/api/pair');
    console.log('[ApiClient] Got pair response:', response);
    this.pair = response.pair;
    // Also store the default pair
    this.defaultPair = response.pair;
    return this.pair;
  }

  setPair(pair) {
    console.log(`[ApiClient] Setting pair to: ${pair}`);
    this.pair = pair;
  }

  async get(url) {
    console.log(`[ApiClient] GET ${url}`);
    const response = await fetch(`${this.baseUrl}${url}`);
    console.log(`[ApiClient] Response status: ${response.status}`);
    if (!response.ok) {
      const text = await response.text();
      console.error(`[ApiClient] Error response: ${text}`);
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
