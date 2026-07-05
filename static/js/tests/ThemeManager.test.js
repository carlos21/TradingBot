import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

function createMockStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  let shouldThrow = false;
  return {
    _data: data,
    _throw() { shouldThrow = true; },
    getItem(key) {
      if (shouldThrow) throw new Error('storage error');
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      if (shouldThrow) throw new Error('storage error');
      data.set(key, String(value));
    },
    removeItem(key) {
      if (shouldThrow) throw new Error('storage error');
      data.delete(key);
    },
  };
}

async function importThemeManager() {
  vi.resetModules();
  await import('../ThemeManager.js');
}

describe('ThemeManager', () => {
  beforeEach(() => {
    document.documentElement.style.cssText = '';
    delete window.LiquidTheme;
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('exposes LiquidTheme API with dark and light themes', async () => {
    vi.stubGlobal('localStorage', createMockStorage());
    await importThemeManager();

    expect(window.LiquidTheme).toBeDefined();
    expect(window.LiquidTheme.list.dark).toBeDefined();
    expect(window.LiquidTheme.list.light).toBeDefined();
    expect(window.LiquidTheme.apply).toBeInstanceOf(Function);
  });

  it('applies dark theme by default and sets CSS variables', async () => {
    vi.stubGlobal('localStorage', createMockStorage());
    await importThemeManager();

    expect(document.documentElement.style.getPropertyValue('--surface-950')).toBe('11 18 32');
    expect(document.documentElement.style.getPropertyValue('--slate-50')).toBe('248 250 252');
  });

  it('applies saved theme from localStorage', async () => {
    vi.stubGlobal('localStorage', createMockStorage({ 'liquid-theme': 'light' }));
    await importThemeManager();

    expect(document.documentElement.style.getPropertyValue('--surface-950')).toBe('255 255 255');
    expect(document.documentElement.style.getPropertyValue('--slate-50')).toBe('15 23 42');
  });

  it('falls back to dark for invalid saved theme', async () => {
    vi.stubGlobal('localStorage', createMockStorage({ 'liquid-theme': 'invalid' }));
    await importThemeManager();

    expect(document.documentElement.style.getPropertyValue('--surface-950')).toBe('11 18 32');
  });

  it('applies light theme via API and persists to localStorage', async () => {
    const storage = createMockStorage();
    vi.stubGlobal('localStorage', storage);
    await importThemeManager();

    window.LiquidTheme.apply('light');

    expect(document.documentElement.style.getPropertyValue('--surface-950')).toBe('255 255 255');
    expect(storage.getItem('liquid-theme')).toBe('light');
  });

  it('ignores localStorage errors when applying theme', async () => {
    const storage = createMockStorage();
    storage._throw();
    vi.stubGlobal('localStorage', storage);

    await expect(importThemeManager()).resolves.not.toThrow();
  });

  it('updates theme selector if present', async () => {
    document.body.innerHTML = `<select id="theme-selector">
      <option value="dark">Dark</option>
      <option value="light">Light</option>
    </select>`;
    vi.stubGlobal('localStorage', createMockStorage({ 'liquid-theme': 'light' }));
    await importThemeManager();

    expect(document.getElementById('theme-selector').value).toBe('light');
  });

  it('listens to theme selector changes', async () => {
    document.body.innerHTML = `<select id="theme-selector">
      <option value="dark">Dark</option>
      <option value="light">Light</option>
    </select>`;
    vi.stubGlobal('localStorage', createMockStorage());
    await importThemeManager();

    const selector = document.getElementById('theme-selector');
    selector.value = 'light';
    selector.dispatchEvent(new Event('change', { bubbles: true }));

    expect(document.documentElement.style.getPropertyValue('--surface-950')).toBe('255 255 255');
  });

  it('waits for DOMContentLoaded when document is still loading', async () => {
    vi.stubGlobal('localStorage', createMockStorage());
    const originalReadyState = document.readyState;
    Object.defineProperty(document, 'readyState', { value: 'loading', configurable: true });

    let fired = false;
    const originalAddEventListener = document.addEventListener;
    document.addEventListener = function (event, handler) {
      if (event === 'DOMContentLoaded') {
        fired = true;
        handler();
      }
    };

    await importThemeManager();
    expect(fired).toBe(true);

    document.addEventListener = originalAddEventListener;
    Object.defineProperty(document, 'readyState', { value: originalReadyState, configurable: true });
  });
});