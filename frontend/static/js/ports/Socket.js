/**
 * Abstract socket subscriber port.
 */
export class ISocketSubscriber {
  on(event, handler) { throw new Error('ISocketSubscriber.on not implemented'); }
  off(event, handler) { throw new Error('ISocketSubscriber.off not implemented'); }
  once(event, handler) { throw new Error('ISocketSubscriber.once not implemented'); }
}

/**
 * Abstract socket publisher port.
 */
export class ISocketPublisher {
  emit(event, payload) { throw new Error('ISocketPublisher.emit not implemented'); }
}

/**
 * Combined socket port for convenience.
 */
export class ISocket extends ISocketSubscriber {
  emit(event, payload) { throw new Error('ISocket.emit not implemented'); }
}
