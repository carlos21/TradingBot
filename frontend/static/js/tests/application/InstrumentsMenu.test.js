import { describe, it, expect, beforeEach } from 'vitest';
import { InstrumentsMenuController } from '../../application/InstrumentsMenuController.js';
import { FakeDomService } from '../fakes/FakeDomService.js';

const instruments = [
  { symbol: 'MNQ', full_name: 'MNQ 09-26', point_value: 2 },
  { symbol: 'MES', full_name: 'MES 09-26', point_value: 5 },
];

function setupDocument() {
  document.body.innerHTML = `
    <button id="instrumentsMenuBtn">Instruments ▾</button>
    <div id="instrumentsMenu" class="hidden"></div>
  `;
}

function buildMenu() {
  const dom = new FakeDomService(document, window);
  const menu = new InstrumentsMenuController(dom);
  menu.init();
  return { dom, menu };
}

describe('InstrumentsMenuController', () => {
  beforeEach(() => {
    setupDocument();
  });

  describe('setInstruments', () => {
    it('renders one entry per instrument linking to a new pinned tab', () => {
      const { menu } = buildMenu();
      menu.setInstruments(instruments, 'MNQ');

      const links = document.querySelectorAll('#instrumentsMenu a');
      expect(links).toHaveLength(2);

      expect(links[0].getAttribute('href')).toBe('/?pair=MNQ');
      expect(links[0].getAttribute('target')).toBe('_blank');
      expect(links[0].getAttribute('rel')).toBe('noopener');
      expect(links[0].textContent).toContain('Open MNQ in new tab ↗');

      expect(links[1].getAttribute('href')).toBe('/?pair=MES');
      expect(links[1].textContent).toContain('Open MES in new tab ↗');
    });

    it('marks the entry for the current instrument', () => {
      const { menu } = buildMenu();
      menu.setInstruments(instruments, 'MES');

      const links = document.querySelectorAll('#instrumentsMenu a');
      expect(links[0].classList.contains('current')).toBe(false);
      expect(links[0].getAttribute('aria-current')).toBe(null);

      expect(links[1].classList.contains('current')).toBe(true);
      expect(links[1].getAttribute('aria-current')).toBe('true');
      expect(links[1].textContent).toContain('(current)');
    });

    it('replaces previously rendered entries', () => {
      const { menu } = buildMenu();
      menu.setInstruments(instruments, 'MNQ');
      menu.setInstruments([instruments[0]], 'MNQ');

      expect(document.querySelectorAll('#instrumentsMenu a')).toHaveLength(1);
    });
  });

  describe('toggle behavior', () => {
    it('toggles the menu on button click', () => {
      buildMenu();
      const panel = document.getElementById('instrumentsMenu');
      const btn = document.getElementById('instrumentsMenuBtn');

      expect(panel.classList.contains('hidden')).toBe(true);
      btn.click();
      expect(panel.classList.contains('hidden')).toBe(false);
      btn.click();
      expect(panel.classList.contains('hidden')).toBe(true);
    });

    it('closes on outside click', () => {
      buildMenu();
      const panel = document.getElementById('instrumentsMenu');

      document.getElementById('instrumentsMenuBtn').click();
      expect(panel.classList.contains('hidden')).toBe(false);

      document.body.click();
      expect(panel.classList.contains('hidden')).toBe(true);
    });

    it('stays open when clicking inside the menu', () => {
      const { menu } = buildMenu();
      menu.setInstruments(instruments, 'MNQ');
      const panel = document.getElementById('instrumentsMenu');

      document.getElementById('instrumentsMenuBtn').click();
      panel.querySelector('a').click();
      expect(panel.classList.contains('hidden')).toBe(false);
    });

    it('closes on Escape', () => {
      buildMenu();
      const panel = document.getElementById('instrumentsMenu');

      document.getElementById('instrumentsMenuBtn').click();
      expect(panel.classList.contains('hidden')).toBe(false);

      document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      expect(panel.classList.contains('hidden')).toBe(true);
    });
  });
});
