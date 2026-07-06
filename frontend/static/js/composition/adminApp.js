import { BrowserDomService } from '../adapters/browser/BrowserDomService.js';
import { FetchHttpClient } from '../adapters/browser/FetchHttpClient.js';
import { IoSocketAdapter } from '../adapters/browser/IoSocketAdapter.js';
import { BrowserNotificationAdapter } from '../adapters/browser/BrowserNotificationAdapter.js';
import { ApiClient } from '../admin/ApiClient.js';
import { AdminDashboardController } from '../application/AdminDashboardController.js';

/**
 * Composition root for the admin dashboard page.
 * Wires browser adapters and the admin dashboard controller.
 */
export function createAdminApp(opts = {}) {
  const dom = new BrowserDomService();
  const http = new FetchHttpClient(window.TRADINGBOT_API_URL || '');
  const api = new ApiClient({ httpClient: http });
  const socket = opts.socket || (typeof io !== 'undefined' ? new IoSocketAdapter(io()) : null);
  const notification = new BrowserNotificationAdapter();

  const controller = new AdminDashboardController({
    api,
    domService: dom,
    socket,
    notification,
  });

  return { app: controller, controller, socket };
}
