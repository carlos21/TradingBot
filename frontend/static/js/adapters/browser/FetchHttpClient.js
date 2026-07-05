import { IHttpClient } from '../../ports/HttpClient.js';

/**
 * HTTP client adapter using the browser fetch API.
 */
export class FetchHttpClient extends IHttpClient {
  constructor(baseUrl = '') {
    super();
    this.baseUrl = baseUrl;
  }

  async get(url) {
    const response = await fetch(`${this.baseUrl}${url}`);
    return this._handleResponse(response);
  }

  async post(url, data) {
    const response = await fetch(`${this.baseUrl}${url}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return this._handleResponse(response);
  }

  async put(url, data) {
    const response = await fetch(`${this.baseUrl}${url}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return this._handleResponse(response);
  }

  async delete(url) {
    const response = await fetch(`${this.baseUrl}${url}`, { method: 'DELETE' });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.status === 204 ? null : response.json();
  }

  async _handleResponse(response) {
    if (!response.ok) {
      const text = await response.text().catch(() => '');
      throw new Error(`HTTP ${response.status}: ${response.statusText} ${text}`);
    }
    return response.json();
  }
}
