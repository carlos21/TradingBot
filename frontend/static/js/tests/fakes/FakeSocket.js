import { ISocket } from '../../ports/Socket.js';

/**
 * In-memory socket for tests.
 */
export class FakeSocket extends ISocket {
  constructor() {
    super();
    this.handlers = new Map();
    this.emissions = [];
  }

  on(event, handler) {
    if (!this.handlers.has(event)) this.handlers.set(event, []);
    this.handlers.get(event).push(handler);
  }

  off(event, handler) {
    const list = this.handlers.get(event) || [];
    this.handlers.set(
      event,
      list.filter(h => h !== handler)
    );
  }

  once(event, handler) {
    const wrapped = (...args) => {
      this.off(event, wrapped);
      handler(...args);
    };
    this.on(event, wrapped);
  }

  emit(event, payload) {
    this.emissions.push({ event, payload });
  }

  trigger(event, payload) {
    for (const handler of this.handlers.get(event) || []) {
      handler(payload);
    }
  }
}
