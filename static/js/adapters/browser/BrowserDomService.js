import { IDomService } from '../../ports/DomService.js';

/**
 * DOM service adapter using the browser's document/window globals.
 */
export class BrowserDomService extends IDomService {
  constructor(windowRef = window, documentRef = document) {
    super();
    this._window = windowRef;
    this._document = documentRef;
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
