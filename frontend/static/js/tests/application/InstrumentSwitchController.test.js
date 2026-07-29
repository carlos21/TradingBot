import { describe, it, expect, beforeEach, vi } from 'vitest';
import { InstrumentSwitchController } from '../../application/InstrumentSwitchController.js';
import { FakeDomService } from '../fakes/FakeDomService.js';

const instrument = { symbol: 'MES', full_name: 'MES 09-26', point_value: 5 };

function setupDocument() {
  document.body.innerHTML = `
    <span id="currentInstrumentLabel"></span>
    <p id="overlayInstrumentLabel"></p>
  `;
}

function buildSwitcher({ search = '?tf=5m&pair=MNQ' } = {}) {
  const win = {
    location: { search, pathname: '/' },
    history: { replaceState: vi.fn() },
  };
  const dom = new FakeDomService(document, win);
  const deps = {
    socket: { emit: vi.fn() },
    chartController: { setActiveInstrument: vi.fn() },
    lifecycle: { setStartSymbol: vi.fn() },
    healthPanel: { setPair: vi.fn() },
    accountsDisplay: { setActiveSymbol: vi.fn() },
    dom,
  };
  const switcher = new InstrumentSwitchController(deps);
  return { switcher, deps, win };
}

describe('InstrumentSwitchController', () => {
  beforeEach(() => {
    setupDocument();
  });

  it('leaves the old room before joining the new one', () => {
    const { switcher, deps } = buildSwitcher();

    switcher.switchTo('MES', instrument, 'MNQ');

    expect(deps.socket.emit.mock.calls.map(c => c[0])).toEqual([
      'leave_instrument',
      'join_instrument',
    ]);
    expect(deps.socket.emit.mock.calls[0][1]).toEqual({ pair: 'MNQ' });
    expect(deps.socket.emit.mock.calls[1][1]).toEqual({ pair: 'MES' });
  });

  it('does not emit leave_instrument when there was no previous pair', () => {
    const { switcher, deps } = buildSwitcher();

    switcher.switchTo('MES', instrument, null);

    expect(deps.socket.emit).toHaveBeenCalledTimes(1);
    expect(deps.socket.emit).toHaveBeenCalledWith('join_instrument', { pair: 'MES' });
  });

  it('is a no-op when switching to the current pair', () => {
    const { switcher, deps } = buildSwitcher();

    switcher.switchTo('MNQ', instrument, 'MNQ');

    expect(deps.socket.emit).not.toHaveBeenCalled();
    expect(deps.chartController.setActiveInstrument).not.toHaveBeenCalled();
  });

  it('is a no-op without a pair', () => {
    const { switcher, deps } = buildSwitcher();

    switcher.switchTo(null, instrument, 'MNQ');

    expect(deps.socket.emit).not.toHaveBeenCalled();
  });

  it('moves the chart, start symbol, health panel, and accounts to the new instrument', () => {
    const { switcher, deps } = buildSwitcher();

    switcher.switchTo('MES', instrument, 'MNQ');

    expect(deps.chartController.setActiveInstrument).toHaveBeenCalledWith('MES', instrument);
    expect(deps.lifecycle.setStartSymbol).toHaveBeenCalledWith('MES');
    expect(deps.healthPanel.setPair).toHaveBeenCalledWith('MES');
    expect(deps.accountsDisplay.setActiveSymbol).toHaveBeenCalledWith('MES');
  });

  it('updates header and overlay labels with the full instrument name', () => {
    const { switcher } = buildSwitcher();

    switcher.switchTo('MES', instrument, 'MNQ');

    expect(document.getElementById('currentInstrumentLabel').textContent).toBe('MES 09-26');
    expect(document.getElementById('overlayInstrumentLabel').textContent).toBe('MES — MES 09-26');
  });

  it('falls back to the bare symbol when full_name is missing', () => {
    const { switcher } = buildSwitcher();

    switcher.switchTo('MES', { symbol: 'MES' }, 'MNQ');

    expect(document.getElementById('currentInstrumentLabel').textContent).toBe('MES');
    expect(document.getElementById('overlayInstrumentLabel').textContent).toBe('MES');
  });

  it('pins the tab to the new pair via replaceState without reloading', () => {
    const { switcher, win } = buildSwitcher({ search: '?tf=5m&pair=MNQ&show_tsi=true' });

    switcher.switchTo('MES', instrument, 'MNQ');

    expect(win.history.replaceState).toHaveBeenCalledTimes(1);
    const url = win.history.replaceState.mock.calls[0][2];
    const params = new URLSearchParams(url.split('?')[1]);
    expect(params.get('pair')).toBe('MES');
    expect(params.get('tf')).toBe('5m');
    expect(params.get('show_tsi')).toBe('true');
  });
});
