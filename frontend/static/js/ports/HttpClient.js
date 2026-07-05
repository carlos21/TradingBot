/**
 * Abstract HTTP client port.
 * Concrete adapters implement these methods using fetch, axios, etc.
 */
export class IHttpClient {
  async get(url) { throw new Error('IHttpClient.get not implemented'); }
  async post(url, data) { throw new Error('IHttpClient.post not implemented'); }
  async put(url, data) { throw new Error('IHttpClient.put not implemented'); }
  async delete(url) { throw new Error('IHttpClient.delete not implemented'); }
}
