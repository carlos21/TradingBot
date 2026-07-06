import { describe, it, expect, beforeEach, vi } from 'vitest';
import { BrowserDomService } from '../../adapters/browser/BrowserDomService.js';

describe('BrowserDomService', () => {
  let doc;
  let win;
  let service;

  beforeEach(() => {
    doc = document;
    win = window;
    doc.body.innerHTML = `
      <div id="test-el">
        <span class="child"></span>
        <span class="child"></span>
      </div>
    `;
    service = new BrowserDomService(win, doc);
  });

  it('returns element by id', () => {
    expect(service.getElementById('test-el')).toBe(doc.getElementById('test-el'));
  });

  it('queries a single element', () => {
    expect(service.querySelector('.child')).toBe(doc.querySelector('.child'));
  });

  it('queries all matching elements as an array', () => {
    expect(service.querySelectorAll('.child')).toHaveLength(2);
  });

  it('creates an element', () => {
    const el = service.createElement('div');
    expect(el.tagName).toBe('DIV');
  });

  it('adds and removes event listeners', () => {
    const el = doc.createElement('button');
    const handler = vi.fn();
    service.addEventListener(el, 'click', handler);
    el.click();
    expect(handler).toHaveBeenCalledTimes(1);
    service.removeEventListener(el, 'click', handler);
    el.click();
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('returns window, document, and location', () => {
    expect(service.getWindow()).toBe(win);
    expect(service.getDocument()).toBe(doc);
    expect(service.getLocation()).toBe(win.location);
  });
});
