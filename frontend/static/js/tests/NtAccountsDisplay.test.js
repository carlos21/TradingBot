import { describe, it, expect, beforeEach, vi } from 'vitest';
import { NtAccountsDisplay } from '../NtAccountsDisplay.js';

function mockFetch(response) {
  return vi.spyOn(global, 'fetch').mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => response,
  });
}

describe('NtAccountsDisplay', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="ntAccountsDisplay"></div>
      <div id="ntAccountsWarning" class="hidden"><a href="#">Settings</a></div>
      <div id="testTradeControls"></div>
    `;
    vi.restoreAllMocks();
  });

  it('loads and renders accounts as pills', async () => {
    mockFetch([
      { name: 'AccountA', risk_usd: 100, risk_pct: 1, rr_ratio: 2 },
      { name: 'AccountB', risk_usd: 200, risk_pct: 2, rr_ratio: 3 },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const container = document.getElementById('ntAccountsDisplay');
    expect(container.textContent).toContain('NT');
    expect(container.textContent).toContain('AccountA');
    expect(container.textContent).toContain('AccountB');
    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(true);
  });

  it('renders overflow button when more than 2 accounts', async () => {
    mockFetch([
      { name: 'A' },
      { name: 'B' },
      { name: 'C' },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const container = document.getElementById('ntAccountsDisplay');
    expect(container.textContent).toContain('+1');
  });

  it('shows popover with all accounts when overflow clicked', async () => {
    mockFetch([
      { name: 'A' },
      { name: 'B' },
      { name: 'C' },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const overflowBtn = document.querySelector('#ntAccountsDisplay button');
    overflowBtn.click();

    const popover = document.querySelector('body > div.absolute');
    expect(popover).toBeTruthy();
    expect(popover.textContent).toContain('All Accounts (3)');
    expect(popover.textContent).toContain('A');
    expect(popover.textContent).toContain('B');
    expect(popover.textContent).toContain('C');
  });

  it('closes popover on second overflow click', async () => {
    mockFetch([
      { name: 'A' },
      { name: 'B' },
      { name: 'C' },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const overflowBtn = document.querySelector('#ntAccountsDisplay button');
    overflowBtn.click();
    expect(document.querySelector('body > div.absolute')).toBeTruthy();

    overflowBtn.click();
    expect(document.querySelector('body > div.absolute')).toBeFalsy();
  });

  it('closes popover on outside click', async () => {
    mockFetch([
      { name: 'A' },
      { name: 'B' },
      { name: 'C' },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const overflowBtn = document.querySelector('#ntAccountsDisplay button');
    overflowBtn.click();
    expect(document.querySelector('body > div.absolute')).toBeTruthy();

    document.body.click();
    expect(document.querySelector('body > div.absolute')).toBeFalsy();
  });

  it('renders warning when accounts are empty', async () => {
    mockFetch([]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('ntAccountsWarning').querySelector('a').getAttribute('href')).toBe('/admin/settings');
    expect(document.getElementById('testTradeControls').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('ntAccountsDisplay').textContent).toContain('No accounts');
  });

  it('handles fetch error gracefully', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network down'));

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsDisplay').textContent).toContain('No accounts');
    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(false);
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it('handles non-ok response', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({ ok: false, status: 500 });

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsDisplay').textContent).toContain('No accounts');
  });

  it('renders account strings as well as objects', async () => {
    mockFetch(['AccountA', 'AccountB']);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsDisplay').textContent).toContain('AccountA');
  });

  it('formats tooltip with risk info', async () => {
    mockFetch([{ name: 'A', risk_usd: 100, risk_pct: 1, rr_ratio: 2 }]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const badge = document.querySelector('#ntAccountsDisplay span.bg-emerald-600');
    expect(badge.title).toBe('$100 risk · 1% risk · 2:1 RR');
  });

  it('init calls loadAccounts', async () => {
    mockFetch([{ name: 'A' }]);
    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    const loadSpy = vi.spyOn(display, 'loadAccounts').mockResolvedValue();

    await display.init();

    expect(loadSpy).toHaveBeenCalled();
  });

  it('renders no accounts when render is called with null', async () => {
    mockFetch([]);
    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    display.render(null, null);
    expect(document.getElementById('ntAccountsDisplay').textContent).toContain('No accounts');
  });

  it('uses singular account tooltip when exactly one account is hidden', async () => {
    mockFetch([
      { name: 'A' },
      { name: 'B' },
      { name: 'C' },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const overflowBtn = document.querySelector('#ntAccountsDisplay button');
    expect(overflowBtn.title).toBe('1 more account');
  });

  it('shows popover with string account names', async () => {
    mockFetch(['A', 'B', 'C']);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const overflowBtn = document.querySelector('#ntAccountsDisplay button');
    overflowBtn.click();

    const popover = document.querySelector('body > div.absolute');
    expect(popover.textContent).toContain('A');
    expect(popover.textContent).toContain('B');
    expect(popover.textContent).toContain('C');
  });
});