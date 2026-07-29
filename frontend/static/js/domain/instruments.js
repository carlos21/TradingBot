/**
 * Pure helpers for resolving the instrument a chart tab is pinned to.
 * Each browser tab is pinned to one instrument via the `?pair=` URL param;
 * switching instruments means reloading the tab with a different `pair`.
 * There is no fallback instrument: without a valid `?pair=` the tab pins
 * nothing and the user must pick an instrument explicitly.
 */

/**
 * Resolve the pinned pair for this tab.
 * Returns the URL pair only when it matches a known instrument symbol,
 * otherwise null (no implicit default).
 *
 * @param {string|null} urlPair - value of the `?pair=` URL param
 * @param {Array<{symbol: string}>} instruments - instrument catalog
 * @returns {string|null}
 */
export function resolvePinnedPair(urlPair, instruments) {
  const list = Array.isArray(instruments) ? instruments : [];
  if (urlPair && list.some(i => i.symbol === urlPair)) {
    return urlPair;
  }
  return null;
}

/**
 * Find an instrument in the catalog by symbol.
 *
 * @param {Array<{symbol: string}>} instruments - instrument catalog
 * @param {string|null} symbol
 * @returns {object|null} the instrument, or null when not found
 */
export function findInstrument(instruments, symbol) {
  if (!symbol || !Array.isArray(instruments)) return null;
  return instruments.find(i => i.symbol === symbol) || null;
}
