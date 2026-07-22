import { BrowserDomService } from '../adapters/browser/BrowserDomService.js';
import { FetchHttpClient } from '../adapters/browser/FetchHttpClient.js';
import { IoSocketAdapter } from '../adapters/browser/IoSocketAdapter.js';
import { BrowserStorageAdapter } from '../adapters/browser/BrowserStorageAdapter.js';
import { BrowserNotificationAdapter } from '../adapters/browser/BrowserNotificationAdapter.js';
import { LightweightChartsAdapter } from '../adapters/charts/LightweightChartsAdapter.js';
import { HttpTradeService } from '../adapters/HttpTradeService.js';
import { ChartController } from '../application/ChartController.js';
import { ChartSocketController } from '../application/ChartSocketController.js';
import { ReplayControlsController } from '../application/ReplayControlsController.js';
import { StreamingControlsController } from '../application/StreamingControlsController.js';
import { StreamingLifecycleController } from '../application/StreamingLifecycleController.js';

/**
 * Composition root for the chart page.
 * Wires adapters, controllers, and the socket mapper.
 */
export function createChartApp(opts = {}) {
  const dom = new BrowserDomService();
  const http = new FetchHttpClient(window.TRADINGBOT_API_URL || '');
  const socket = new IoSocketAdapter(opts.socket || io());
  const chartApi = new LightweightChartsAdapter(LightweightCharts);
  const storage = BrowserStorageAdapter.session();
  const notification = new BrowserNotificationAdapter();
  const tradeService = new HttpTradeService(http);

  const controller = new ChartController({
    domService: dom,
    httpClient: http,
    socket,
    chartApi,
    storage,
    options: {
      activePair: opts.options?.activePair,
      activeInstrument: opts.options?.activeInstrument,
      ...opts.options,
    },
  });

  // Single owner of the streaming-control UI state (Start/Reconnect/Stop
  // buttons + connection overlay). Both the click and socket controllers
  // dispatch lifecycle events into it.
  const streamingLifecycle = new StreamingLifecycleController(dom);
  streamingLifecycle.init();

  const socketController = new ChartSocketController(socket, controller, dom, streamingLifecycle);
  socketController.init();

  const controls = new ReplayControlsController(controller, socket, dom, notification, tradeService);
  controls.init();

  const streamingControls = new StreamingControlsController(dom, notification, streamingLifecycle);
  streamingControls.init();

  return { controller, socketController, controls, streamingControls, streamingLifecycle, socket };
}
