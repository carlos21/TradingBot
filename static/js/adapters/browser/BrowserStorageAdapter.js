import { IStorage } from '../../ports/Storage.js';

/**
 * Browser storage adapter for sessionStorage or localStorage.
 */
export class BrowserStorageAdapter extends IStorage {
  constructor(store) {
    super();
    this.store = store;
  }

  static local() {
    return new BrowserStorageAdapter(localStorage);
  }

  static session() {
    return new BrowserStorageAdapter(sessionStorage);
  }

  getItem(key) {
    try {
      return this.store.getItem(key);
    } catch {
      return null;
    }
  }

  setItem(key, value) {
    try {
      this.store.setItem(key, value);
    } catch {
      // ignore storage errors (private mode, quota exceeded, etc.)
    }
  }

  removeItem(key) {
    try {
      this.store.removeItem(key);
    } catch {
      // ignore
    }
  }
}
