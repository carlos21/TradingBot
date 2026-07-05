/**
 * Abstract storage port (session/local storage abstraction).
 */
export class IStorage {
  getItem(key) { throw new Error('IStorage.getItem not implemented'); }
  setItem(key, value) { throw new Error('IStorage.setItem not implemented'); }
  removeItem(key) { throw new Error('IStorage.removeItem not implemented'); }
}
