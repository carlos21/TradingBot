import { BrowserDomService } from '../adapters/browser/BrowserDomService.js';
import { FetchHttpClient } from '../adapters/browser/FetchHttpClient.js';
import { IoSocketAdapter } from '../adapters/browser/IoSocketAdapter.js';
import { BrowserStorageAdapter } from '../adapters/browser/BrowserStorageAdapter.js';
import { BrowserNotificationAdapter } from '../adapters/browser/BrowserNotificationAdapter.js';
import { LightweightChartsAdapter } from '../adapters/charts/LightweightChartsAdapter.js';
import { ChartController } from '../application/ChartController.js';
import { ChartSocketController } from '../application/ChartSocketController.js';
import { ReplayControlsController } from '../application/ReplayControlsController.js';

/**
 * Composition root for the chart page.
 * Wires adapters, controllers, and the socket mapper.
 */
export function createChartApp(opts = {}) {
  const dom = new BrowserDomService();
  const http = new FetchHttpClient();
  const socket = new IoSocketAdapter(opts.socket || io());
  const chartApi = new LightweightChartsAdapter(LightweightCharts);
  const storage = BrowserStorageAdapter.session();
  const notification = new BrowserNotificationAdapter();

  const controller = new ChartController({
    domService: dom,
    httpClient: http,
    socket,
    chartApi,
    storage,
    options: opts.options,
  });

  const socketController = new ChartSocketController(socket, controller, dom);
  socketController.init();

  const controls = new ReplayControlsController(controller, socket, dom, notification);
  controls.init();

  return { controller, socketController, controls, socket };
}
