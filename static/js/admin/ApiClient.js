/**
 * API Client for Admin Dashboard
 * Handles all HTTP requests to the backend
 */
export class ApiClient {
  constructor({ httpClient }) {
    this.http = httpClient;
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
  async getStats() {
    return this.get(`/api/admin/stats?pair=${this.pair}`);
  }

  async getTrades(limit = 50, offset = 0, account = '') {
    let url = `/api/admin/trades?pair=${this.pair}&limit=${limit}&offset=${offset}`;
    if (account) url += `&account=${encodeURIComponent(account)}`;
    return this.get(url);
  }

  async getTradeAccounts() {
    return this.get(`/api/admin/trade-accounts?pair=${this.pair}`);
  }

  async getTradeDetail(tradeId) {
    return this.get(`/api/admin/trades/${tradeId}`);
  }

  async deleteTrade(tradeId) {
    return this.delete(`/api/admin/trades/${tradeId}`);
  }

  async getAnalytics() {
    return this.get(`/api/admin/analytics?pair=${this.pair}`);
  }

  async getLines() {
    return this.get(`/api/lines?pair=${this.pair}`);
  }

  async getDecisionLogs(event = '', lineId = '', limit = 500) {
    let url = `/api/admin/decisions?pair=${this.pair}&limit=${limit}`;
    if (event) url += `&event=${encodeURIComponent(event)}`;
    if (lineId) url += `&line_id=${encodeURIComponent(lineId)}`;
    return this.get(url);
  }

  async getDecisionEvents() {
    return this.get('/api/admin/decisions/events');
  }

  async getRecentLogs(limit = 200, offset = 0) {
    return this.get(`/api/admin/logs/recent?pair=${this.pair}&limit=${limit}&offset=${offset}`);
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
