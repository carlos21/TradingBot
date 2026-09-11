import { describe, it, expect, beforeEach, vi } from 'vitest';
import { NtAccountsDisplay } from '../NtAccountsDisplay.js';
import { FakeSocket } from './fakes/FakeSocket.js';

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
      <div id="ntAccountsWarning" class="hidden">
        <span id="ntAccountsWarningMessage">⚠️ No NT accounts configured — live trading is disabled.</span>
        <a href="#">Settings</a>
      </div>
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

  it('reloads accounts when the socket connects', async () => {
    mockFetch([{ name: 'A' }]);
    const socket = new FakeSocket();
    const display = new NtAccountsDisplay(socket);
    const loadSpy = vi.spyOn(display, 'loadAccounts').mockResolvedValue();

    display.init();
    expect(loadSpy).toHaveBeenCalledTimes(1);

    socket.trigger('connect');
    expect(loadSpy).toHaveBeenCalledTimes(2);
  });

  it('recovers from an initial fetch failure once the socket connects', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const fetchSpy = vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => [{ name: 'RecoveredAccount' }],
      });

    const socket = new FakeSocket();
    const display = new NtAccountsDisplay(socket);
    display.init();
    await vi.waitFor(() => {
      expect(document.getElementById('ntAccountsDisplay').textContent).toContain('No accounts');
    });

    socket.trigger('connect');
    await vi.waitFor(() => {
      expect(document.getElementById('ntAccountsDisplay').textContent).toContain('RecoveredAccount');
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(true);
    errorSpy.mockRestore();
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

  it('renders only live-enabled accounts', async () => {
    mockFetch([
      { name: 'LiveOne', live_enabled: true },
      { name: 'DisabledOne', live_enabled: false },
      { name: 'LiveTwo', live_enabled: true },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const container = document.getElementById('ntAccountsDisplay');
    expect(container.textContent).toContain('LiveOne');
    expect(container.textContent).toContain('LiveTwo');
    expect(container.textContent).not.toContain('DisabledOne');
  });

  it('excludes non-live accounts from the overflow popover', async () => {
    mockFetch([
      { name: 'A', live_enabled: true },
      { name: 'B', live_enabled: false },
      { name: 'C', live_enabled: true },
      { name: 'D', live_enabled: false },
      { name: 'E', live_enabled: true },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const overflowBtn = document.querySelector('#ntAccountsDisplay button');
    overflowBtn.click();

    const popover = document.querySelector('body > div.absolute');
    expect(popover.textContent).toContain('All Accounts (3)');
    expect(popover.textContent).toContain('A');
    expect(popover.textContent).toContain('C');
    expect(popover.textContent).toContain('E');
    expect(popover.textContent).not.toContain('B');
    expect(popover.textContent).not.toContain('D');
  });

  it('treats accounts without live_enabled as live-enabled for backward compatibility', async () => {
    mockFetch([
      { name: 'LegacyA' },
      { name: 'LegacyB' },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    const container = document.getElementById('ntAccountsDisplay');
    expect(container.textContent).toContain('LegacyA');
    expect(container.textContent).toContain('LegacyB');
  });

  it('shows empty state when all accounts are non-live', async () => {
    mockFetch([
      { name: 'DisabledA', live_enabled: false },
      { name: 'DisabledB', live_enabled: false },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} });
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsDisplay').textContent).toContain('No accounts');
    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('testTradeControls').classList.contains('hidden')).toBe(true);
  });

  it('shows warning when active symbol is not assigned to any live account', async () => {
    mockFetch([
      { name: 'AccountA', live_enabled: true, instrument_symbols: ['MNQ'] },
      { name: 'AccountB', live_enabled: true, instrument_symbols: ['ES'] },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} }, 'MES');
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('ntAccountsWarningMessage').textContent).toContain('MES');
    expect(document.getElementById('testTradeControls').classList.contains('hidden')).toBe(true);
  });

  it('hides warning when active symbol is assigned to a live account', async () => {
    mockFetch([
      { name: 'AccountA', live_enabled: true, instrument_symbols: ['MNQ'] },
      { name: 'AccountB', live_enabled: true, instrument_symbols: ['MES'] },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} }, 'MES');
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('testTradeControls').classList.contains('hidden')).toBe(false);
  });

  it('shows warning when active symbol is assigned only to non-live accounts', async () => {
    mockFetch([
      { name: 'AccountA', live_enabled: false, instrument_symbols: ['MES'] },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} }, 'MES');
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('ntAccountsWarningMessage').textContent).toContain('MES');
  });

  it('ignores activeSymbol when null and falls back to live account count', async () => {
    mockFetch([
      { name: 'AccountA', live_enabled: true, instrument_symbols: ['MNQ'] },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} }, null);
    await display.loadAccounts();

    expect(document.getElementById('ntAccountsWarning').classList.contains('hidden')).toBe(true);
  });

  it('renders only accounts assigned to the active symbol', async () => {
    mockFetch([
      { name: 'MNQAccount', live_enabled: true, instrument_symbols: ['MNQ'] },
      { name: 'MESAccount', live_enabled: true, instrument_symbols: ['MES'] },
      { name: 'BothAccount', live_enabled: true, instrument_symbols: ['MNQ', 'MES'] },
    ]);

    const display = new NtAccountsDisplay({ on: () => {}, emit: () => {} }, 'MES');
    await display.loadAccounts();

    const container = document.getElementById('ntAccountsDisplay');
    expect(container.textContent).toContain('MESAccount');
    expect(container.textContent).toContain('BothAccount');
    expect(container.textContent).not.toContain('MNQAccount');
  });
});