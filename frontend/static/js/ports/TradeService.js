/**
 * Port for trade-related operations from the chart UI.
 * Abstracts the HTTP transport so controllers stay testable.
 */
export class TradeService {
  async openTestTrade(pair, direction) {
    throw new Error('openTestTrade() must be implemented');
  }

  async closeAllTrades(pair) {
    throw new Error('closeAllTrades() must be implemented');
  }

  async listTrades(pair) {
    throw new Error('listTrades() must be implemented');
  }

  async modifyStopLoss(tradeId, stopLoss) {
    throw new Error('modifyStopLoss() must be implemented');
  }
}
