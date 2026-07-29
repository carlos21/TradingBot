import { describe, it, expect, beforeEach } from 'vitest';
import { OverlayInstrumentController } from '../../application/OverlayInstrumentController.js';
import { FakeDomService } from '../fakes/FakeDomService.js';

const instruments = [
  { symbol: 'MNQ', full_name: 'MNQ 09-26', point_value: 2 },
  { symbol: 'MES', full_name: 'MES 09-26', point_value: 5 },
];

function setupDocument() {
  document.body.innerHTML = `
    <p id="overlayInstrumentLabel"></p>
    <select id="overlayInstrumentSelect" class="hidden"></select>
  `;
}

function buildController(location = { search: '' }) {
  const dom = new FakeDomService(document, { location });
  const controller = new OverlayInstrumentController(dom);
  controller.init();
  return { controller, location };
}

describe('OverlayInstrumentController', () => {
  beforeEach(() => {
    setupDocument();
  });

  describe('setInstruments', () => {
    it('keeps the static label when pinned to the only instrument', () => {
      const { controller } = buildController();
      controller.setInstruments([instruments[0]], 'MNQ');

      const select = document.getElementById('overlayInstrumentSelect');
      const label = document.getElementById('overlayInstrumentLabel');
      expect(select.classList.contains('hidden')).toBe(true);
      expect(label.classList.contains('hidden')).toBe(false);
      expect(select.querySelectorAll('option')).toHaveLength(0);
    });

    it('shows the select with a placeholder when nothing is pinned', () => {
      const { controller } = buildController();
      controller.setInstruments(instruments, null);

      const select = document.getElementById('overlayInstrumentSelect');
      const label = document.getElementById('overlayInstrumentLabel');
      expect(select.classList.contains('hidden')).toBe(false);
      expect(label.classList.contains('hidden')).toBe(true);

      const options = select.querySelectorAll('option');
      expect(options).toHaveLength(3);
      expect(options[0].value).toBe('');
      expect(options[0].textContent).toBe('Select instrument…');
      expect(options[0].disabled).toBe(true);
      expect(options[0].selected).toBe(true);
      expect(options[1].value).toBe('MNQ');
      expect(options[2].value).toBe('MES');
    });

    it('shows the select with a placeholder even for a single instrument when unpinned', () => {
      const { controller } = buildController();
      controller.setInstruments([instruments[0]], null);

      const select = document.getElementById('overlayInstrumentSelect');
      expect(select.classList.contains('hidden')).toBe(false);
      const options = select.querySelectorAll('option');
      expect(options).toHaveLength(2);
      expect(options[0].value).toBe('');
      expect(options[1].value).toBe('MNQ');
    });

    it('keeps the static label when the catalog is empty', () => {
      const { controller } = buildController();
      controller.setInstruments([], null);

      expect(document.getElementById('overlayInstrumentSelect').classList.contains('hidden')).toBe(true);
      expect(document.getElementById('overlayInstrumentLabel').classList.contains('hidden')).toBe(false);
    });

    it('shows the select populated with all instruments when pinned and there are several', () => {
      const { controller } = buildController();
      controller.setInstruments(instruments, 'MNQ');

      const select = document.getElementById('overlayInstrumentSelect');
      const label = document.getElementById('overlayInstrumentLabel');
      expect(select.classList.contains('hidden')).toBe(false);
      expect(label.classList.contains('hidden')).toBe(true);

      const options = select.querySelectorAll('option');
      expect(options).toHaveLength(2);
      expect(options[0].value).toBe('MNQ');
      expect(options[0].textContent).toBe('MNQ — MNQ 09-26');
      expect(options[1].value).toBe('MES');
      expect(options[1].textContent).toBe('MES — MES 09-26');
    });

    it('preselects the pair this tab is pinned to', () => {
      const { controller } = buildController();
      controller.setInstruments(instruments, 'MES');

      expect(document.getElementById('overlayInstrumentSelect').value).toBe('MES');
    });

    it('falls back to the bare symbol when full_name is missing', () => {
      const { controller } = buildController();
      controller.setInstruments([{ symbol: 'MNQ' }, { symbol: 'ES' }], 'MNQ');

      const options = document.querySelectorAll('#overlayInstrumentSelect option');
      expect(options[0].textContent).toBe('MNQ');
    });
  });

  describe('instrument change', () => {
    it('stores the selection without reloading the tab', () => {
      const location = { search: '' };
      const { controller } = buildController(location);
      controller.setInstruments(instruments, 'MNQ');

      const select = document.getElementById('overlayInstrumentSelect');
      select.value = 'MES';
      select.dispatchEvent(new Event('change'));

      expect(controller.getSelectedSymbol()).toBe('MES');
      expect(location.search).toBe('');
    });

    it('notifies onSelectionChange with the picked symbol', () => {
      const { controller } = buildController();
      controller.setInstruments(instruments, null);
      const picked = [];
      controller.onSelectionChange = (symbol) => picked.push(symbol);

      const select = document.getElementById('overlayInstrumentSelect');
      select.value = 'MES';
      select.dispatchEvent(new Event('change'));

      expect(picked).toEqual(['MES']);
    });

    it('defaults the selection to the pinned pair', () => {
      const { controller } = buildController();
      controller.setInstruments(instruments, 'MES');

      expect(controller.getSelectedSymbol()).toBe('MES');
    });

    it('has no selection when unpinned', () => {
      const { controller } = buildController();
      controller.setInstruments(instruments, null);

      expect(controller.getSelectedSymbol()).toBeNull();
    });
  });

  describe('setEnabled', () => {
    it('disables and re-enables the select', () => {
      const { controller } = buildController();
      controller.setInstruments(instruments, 'MNQ');
      const select = document.getElementById('overlayInstrumentSelect');

      controller.setEnabled(false);
      expect(select.disabled).toBe(true);

      controller.setEnabled(true);
      expect(select.disabled).toBe(false);
    });
  });
});
