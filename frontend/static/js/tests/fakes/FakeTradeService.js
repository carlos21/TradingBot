import { vi } from 'vitest';
import { TradeService } from '../../ports/TradeService.js';

/**
 * Test double for TradeService.
 */
export class FakeTradeService extends TradeService {
  constructor() {
    super();
    this.openTestTrade = vi.fn();
    this.closeAllTrades = vi.fn();
    this.listTrades = vi.fn();
    this.modifyStopLoss = vi.fn();
  }
}
