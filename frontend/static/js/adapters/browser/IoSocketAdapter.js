import { ISocket } from '../../ports/Socket.js';

/**
 * Socket.IO adapter implementing the ISocket port.
 */
export class IoSocketAdapter extends ISocket {
  constructor(socket) {
    super();
    this.socket = socket;
  }

  on(event, handler) {
    this.socket.on(event, handler);
    return this;
  }

  off(event, handler) {
    this.socket.off(event, handler);
    return this;
  }

  once(event, handler) {
    this.socket.once(event, handler);
    return this;
  }

  emit(event, payload) {
    this.socket.emit(event, payload);
    return this;
  }
}
