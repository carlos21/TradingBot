import { IDomService } from '../../ports/DomService.js';

/**
 * Minimal fake DOM service for controller tests.
 * Backed by happy-dom when available, otherwise uses a tiny in-memory tree.
 */
export class FakeDomService extends IDomService {
  constructor(documentRef, windowRef) {
    super();
    this._document = documentRef;
    this._window = windowRef;
  }

  getElementById(id) {
    return this._document.getElementById(id);
  }

  querySelector(selector) {
    return this._document.querySelector(selector);
  }

  querySelectorAll(selector) {
    return Array.from(this._document.querySelectorAll(selector));
  }

  createElement(tag) {
    return this._document.createElement(tag);
  }

  addEventListener(target, event, handler) {
    target.addEventListener(event, handler);
  }

  removeEventListener(target, event, handler) {
    target.removeEventListener(event, handler);
  }

  getWindow() {
    return this._window;
  }

  getDocument() {
    return this._document;
  }

  getLocation() {
    return this._window.location;
  }
}
