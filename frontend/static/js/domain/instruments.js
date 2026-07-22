/**
 * Pure helpers for resolving the instrument a chart tab is pinned to.
 * Each browser tab is pinned to one instrument via the `?pair=` URL param;
 * switching instruments means opening another tab, not mutating this one.
 */

/**
 * Resolve the pinned pair for this tab.
 * Priority: URL pair (only if it matches a known instrument symbol),
 * then the instance default pair from /api/config, then the first
 * instrument in the catalog, then null.
 *
 * @param {string|null} urlPair - value of the `?pair=` URL param
 * @param {Array<{symbol: string}>} instruments - instrument catalog
 * @param {string|null|undefined} defaultPair - instance default pair
 * @returns {string|null}
 */
export function resolvePinnedPair(urlPair, instruments, defaultPair) {
  const list = Array.isArray(instruments) ? instruments : [];
  if (urlPair && list.some(i => i.symbol === urlPair)) {
    return urlPair;
  }
  if (defaultPair) {
    return defaultPair;
  }
  return list.length > 0 ? list[0].symbol : null;
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
