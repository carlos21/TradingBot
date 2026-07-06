import { IStorage } from '../../ports/Storage.js';

/**
 * In-memory storage for tests.
 */
export class FakeStorage extends IStorage {
  constructor(initial = {}) {
    super();
    this.data = new Map(Object.entries(initial));
  }

  getItem(key) {
    return this.data.has(key) ? this.data.get(key) : null;
  }

  setItem(key, value) {
    this.data.set(key, String(value));
  }

  removeItem(key) {
    this.data.delete(key);
  }
}
