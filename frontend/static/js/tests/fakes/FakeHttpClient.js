import { IHttpClient } from '../../ports/HttpClient.js';

/**
 * In-memory HTTP client for tests.
 */
export class FakeHttpClient extends IHttpClient {
  constructor() {
    super();
    this.responses = new Map();
    this.requests = [];
  }

  setResponse(method, path, data) {
    this.responses.set(`${method} ${path}`, data);
  }

  _record(method, url, data) {
    this.requests.push({ method, url, data });
  }

  _find(method, url) {
    for (const [key, value] of this.responses) {
      const [respMethod, path] = key.split(' ');
      if (respMethod !== method) continue;
      if (url === path || url.startsWith(path + '?')) {
        return { method: respMethod, value };
      }
    }
    return null;
  }

  async get(url) {
    this._record('GET', url);
    const found = this._find('GET', url);
    if (!found) throw new Error(`FakeHttpClient: no response for GET ${url}`);
    return typeof found.value === 'function' ? found.value() : found.value;
  }

  async post(url, data) {
    this._record('POST', url, data);
    const found = this._find('POST', url);
    if (!found) throw new Error(`FakeHttpClient: no response for POST ${url}`);
    return typeof found.value === 'function' ? found.value(data) : found.value;
  }

  async put(url, data) {
    this._record('PUT', url, data);
    const found = this._find('PUT', url);
    if (!found) throw new Error(`FakeHttpClient: no response for PUT ${url}`);
    return typeof found.value === 'function' ? found.value(data) : found.value;
  }

  async delete(url) {
    this._record('DELETE', url);
    const found = this._find('DELETE', url);
    if (!found) throw new Error(`FakeHttpClient: no response for DELETE ${url}`);
    return typeof found.value === 'function' ? found.value() : found.value;
  }
}
