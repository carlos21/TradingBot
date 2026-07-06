/**
 * Abstract DOM service port.
 * Abstracts document/window so controllers can be tested with a fake DOM.
 */
export class IDomService {
  getElementById(id) { throw new Error('IDomService.getElementById not implemented'); }
  querySelector(selector) { throw new Error('IDomService.querySelector not implemented'); }
  querySelectorAll(selector) { throw new Error('IDomService.querySelectorAll not implemented'); }
  createElement(tag) { throw new Error('IDomService.createElement not implemented'); }
  addEventListener(target, event, handler) { throw new Error('IDomService.addEventListener not implemented'); }
  removeEventListener(target, event, handler) { throw new Error('IDomService.removeEventListener not implemented'); }
  getWindow() { throw new Error('IDomService.getWindow not implemented'); }
  getDocument() { throw new Error('IDomService.getDocument not implemented'); }
  getLocation() { throw new Error('IDomService.getLocation not implemented'); }
}
