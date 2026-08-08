/**
 * Trading mode domain logic (pure, no DOM).
 *
 * The user picks Live Trading or Simulation in the streaming overlay; the
 * choice is persisted so the chart UI keeps showing the current mode across
 * page refreshes, and it is forwarded to the backend so the platform
 * auto-login clicks the matching option.
 */

export const TradingMode = Object.freeze({
  SIMULATION: 'simulation',
  LIVE: 'live',
});

export const TRADING_MODE_STORAGE_KEY = 'tradingBot.tradingMode';

/** Coerce any stored/provided value to a valid mode (Simulation is the safe default). */
export function normalizeTradingMode(value) {
  return value === TradingMode.LIVE ? TradingMode.LIVE : TradingMode.SIMULATION;
}

/** Human-readable label for a mode. */
export function tradingModeLabel(mode) {
  return mode === TradingMode.LIVE ? 'Live Trading' : 'Simulation';
}
