import { describe, it, expect, beforeEach } from 'vitest';
import { BrowserStorageAdapter } from '../../adapters/browser/BrowserStorageAdapter.js';

describe('BrowserStorageAdapter adapter', () => {
  let store;
  let adapter;

  beforeEach(() => {
    store = {
      _data: new Map(),
      getItem(key) { return this._data.get(key) ?? null; },
      setItem(key, value) { this._data.set(key, String(value)); },
      removeItem(key) { this._data.delete(key); },
    };
    adapter = new BrowserStorageAdapter(store);
  });

  it('reads, writes, and removes items', () => {
    expect(adapter.getItem('foo')).toBeNull();
    adapter.setItem('foo', 'bar');
    expect(adapter.getItem('foo')).toBe('bar');
    adapter.removeItem('foo');
    expect(adapter.getItem('foo')).toBeNull();
  });

  it('survives errors gracefully', () => {
    const broken = {
      getItem() { throw new Error('nope'); },
      setItem() { throw new Error('nope'); },
      removeItem() { throw new Error('nope'); },
    };
    const a = new BrowserStorageAdapter(broken);
    expect(a.getItem('x')).toBeNull();
    expect(() => a.setItem('x', 'y')).not.toThrow();
    expect(() => a.removeItem('x')).not.toThrow();
  });

  it('provides local and session factory methods', () => {
    expect(BrowserStorageAdapter.local()).toBeInstanceOf(BrowserStorageAdapter);
    expect(BrowserStorageAdapter.session()).toBeInstanceOf(BrowserStorageAdapter);
  });
});
