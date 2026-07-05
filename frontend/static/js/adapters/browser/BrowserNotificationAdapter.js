import { INotification } from '../../ports/Notification.js';

/**
 * Browser notification adapter using window.alert / window.confirm.
 */
export class BrowserNotificationAdapter extends INotification {
  constructor(windowRef = window) {
    super();
    this._window = windowRef;
  }

  alert(message) {
    this._window.alert(message);
  }

  confirm(message) {
    return this._window.confirm(message);
  }
}
