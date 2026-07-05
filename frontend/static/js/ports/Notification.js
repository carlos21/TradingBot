/**
 * Abstract notification port for user alerts/confirms.
 */
export class INotification {
  alert(message) { throw new Error('INotification.alert not implemented'); }
  confirm(message) { throw new Error('INotification.confirm not implemented'); }
}
