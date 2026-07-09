import { TradeService } from '../ports/TradeService.js';

/**
 * HTTP implementation of TradeService using the configured IHttpClient.
 */
export class HttpTradeService extends TradeService {
  constructor(httpClient) {
    super();
    this.http = httpClient;
  }

  async openTestTrade(pair, direction) {
    return this.http.post('/api/trades/test', { pair, direction });
  }

  async closeAllTrades(pair) {
    return this.http.post('/api/trades/close-all', { pair });
  }

  async listTrades(pair) {
    return this.http.get(`/api/trades?pair=${encodeURIComponent(pair)}`);
  }

  async modifyStopLoss(tradeId, stopLoss) {
    return this.http.post(`/api/trades/${encodeURIComponent(tradeId)}/stop-loss`, {
      stop_loss: stopLoss,
    });
  }
}
