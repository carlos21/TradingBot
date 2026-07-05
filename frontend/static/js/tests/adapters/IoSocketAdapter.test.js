import { describe, it, expect, vi } from 'vitest';
import { IoSocketAdapter } from '../../adapters/browser/IoSocketAdapter.js';

describe('IoSocketAdapter', () => {
  function makeSocket() {
    return {
      on: vi.fn().mockReturnThis(),
      off: vi.fn().mockReturnThis(),
      once: vi.fn().mockReturnThis(),
      emit: vi.fn().mockReturnThis(),
    };
  }

  it('delegates on/off/once/emit and returns this', () => {
    const socket = makeSocket();
    const adapter = new IoSocketAdapter(socket);
    const handler = () => {};

    expect(adapter.on('foo', handler)).toBe(adapter);
    expect(socket.on).toHaveBeenCalledWith('foo', handler);

    expect(adapter.off('foo', handler)).toBe(adapter);
    expect(socket.off).toHaveBeenCalledWith('foo', handler);

    expect(adapter.once('bar', handler)).toBe(adapter);
    expect(socket.once).toHaveBeenCalledWith('bar', handler);

    expect(adapter.emit('baz', { x: 1 })).toBe(adapter);
    expect(socket.emit).toHaveBeenCalledWith('baz', { x: 1 });
  });
});
