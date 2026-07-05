import { INotification } from '../../ports/Notification.js';

/**
 * In-memory notification adapter for tests.
 */
export class FakeNotification extends INotification {
  constructor() {
    super();
    this.alerts = [];
    this.confirms = [];
    this.nextConfirm = true;
  }

  alert(message) {
    this.alerts.push(message);
  }

  confirm(message) {
    this.confirms.push(message);
    return this.nextConfirm;
  }
}
