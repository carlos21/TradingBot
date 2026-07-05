import { describe, it, expect, vi } from 'vitest';
import { BrowserNotificationAdapter } from '../../adapters/browser/BrowserNotificationAdapter.js';

describe('BrowserNotificationAdapter', () => {
  it('calls window.alert', () => {
    const alert = vi.fn();
    const notification = new BrowserNotificationAdapter({ alert });
    notification.alert('hello');
    expect(alert).toHaveBeenCalledWith('hello');
  });

  it('calls window.confirm and returns the result', () => {
    const confirm = vi.fn().mockReturnValue(true);
    const notification = new BrowserNotificationAdapter({ confirm });
    expect(notification.confirm('sure?')).toBe(true);
    expect(confirm).toHaveBeenCalledWith('sure?');
  });
});
