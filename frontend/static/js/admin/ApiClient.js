/**
 * API Client for Admin Dashboard
 * Handles all HTTP requests to the backend
 */
export class ApiClient {
  constructor({ httpClient }) {
    this.http = httpClient;
    this.baseUrl = '';
    this.defaultPair = null;
    this.instruments = [];
  }

  async init() {
    // Get server config: default pair + configured instruments
    console.log('[ApiClient] Initializing, fetching config...');
    const response = await this.get('/api/config');
    console.log('[ApiClient] Got config response:', response);
    this.defaultPair = response.pair;
    this.instruments = Array.isArray(response.instruments) ? response.instruments : [];
    return this.defaultPair;
  }

  async get(url) {
    console.log(`[ApiClient] GET ${url}`);
    return this.http.get(`${this.baseUrl}${url}`);
  }

  async post(url, data) {
    return this.http.post(`${this.baseUrl}${url}`, data);
  }

  async put(url, data) {
    return this.http.put(`${this.baseUrl}${url}`, data);
  }

  async delete(url) {
    return this.http.delete(`${this.baseUrl}${url}`);
  }

  // Settings API Methods
  async getSettings() {
    return this.get('/api/settings');
  }

  async saveSettings(payload) {
    return this.post('/api/settings', payload);
  }

  async getAccounts() {
    return this.get('/api/accounts');
  }

  async saveAccount(data) {
    return this.post('/api/accounts', data);
  }

  async deleteAccount(name) {
    return this.delete(`/api/accounts/${encodeURIComponent(name)}`);
  }

  // NT API Methods
  async installNtNetmq() {
    return this.post('/api/nt/install-netmq', {});
  }

  async openNt(username, password) {
    return this.post('/api/nt/open', { username, password });
  }

  // MT API Methods
  async launchMt(exePath) {
    return this.post('/api/mt/launch', { exe_path: exePath });
  }

  async deployMt() {
    return this.post('/api/mt/deploy', {});
  }

  async deployNt() {
    return this.post('/api/nt/deploy', {});
  }

  // Admin API Methods
  async getStats(pair) {
    return this.get(`/api/admin/stats?pair=${pair}`);
  }

  async getTrades(pair, limit = 50, offset = 0, account = '') {
    let url = `/api/admin/trades?pair=${pair}&limit=${limit}&offset=${offset}`;
    if (account) url += `&account=${encodeURIComponent(account)}`;
    return this.get(url);
  }

  async getTradeAccounts(pair) {
    return this.get(`/api/admin/trade-accounts?pair=${pair}`);
  }

  async getTradeDetail(tradeId) {
    return this.get(`/api/admin/trades/${tradeId}`);
  }

  async deleteTrade(tradeId) {
    return this.delete(`/api/admin/trades/${tradeId}`);
  }

  async deleteTrades(tradeIds) {
    return this.post('/api/admin/trades/bulk-delete', { trade_ids: tradeIds });
  }

  async getAnalytics(pair) {
    return this.get(`/api/admin/analytics?pair=${pair}`);
  }

  async getLines(pair) {
    return this.get(`/api/lines?pair=${pair}`);
  }

  async getDecisionLogs(pair, event = '', lineId = '', limit = 500) {
    let url = `/api/admin/decisions?pair=${pair}&limit=${limit}`;
    if (event) url += `&event=${encodeURIComponent(event)}`;
    if (lineId) url += `&line_id=${encodeURIComponent(lineId)}`;
    return this.get(url);
  }

  async getDecisionEvents() {
    return this.get('/api/admin/decisions/events');
  }

  async getRecentLogs(pair, limit = 200, offset = 0) {
    return this.get(`/api/admin/logs/recent?pair=${pair}&limit=${limit}&offset=${offset}`);
  }

  async addLine(pair, price) {
    return this.post('/api/lines', { pair, price });
  }

  async updateLine(lineId, price) {
    return this.put(`/api/lines/${lineId}`, { price });
  }

  async deleteLine(lineId) {
    return this.delete(`/api/lines/${lineId}`);
  }
}
