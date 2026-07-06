/**
 * Pure decision-log helpers.
 * No DOM or I/O dependencies.
 */

export const BADGE = {
  ENTRY: 'entry',
  BLOCK: 'block',
  PENDING: 'pending',
  REMOVE: 'remove',
  INFO: 'info',
  LONG: 'long',
  SHORT: 'short',
};

const ENTRY_EVENTS = new Set([
  'ENTRY',
  'LATCH',
  'VAT_CROSS_2',
  'TSI_CROSS',
]);

const BLOCK_EVENTS = new Set([
  'FILTER_BLOCK',
  'TSI_INVALID',
  'VAT_CROSS1_TOO_FAR',
]);

const PENDING_EVENTS = new Set([
  'TRIGGER_SKIP',
  'LATCH_PENDING',
  'TSI_FAST',
]);

const INFO_EVENTS = new Set([
  'VAT_REGIME',
  'TSI_RESET',
  'VAT_RESET',
  'TSI_SWEEP',
]);

/**
 * Map a decision-log event name to a semantic badge category.
 */
export function getEventBadgeCategory(event) {
  if (!event) return BADGE.REMOVE;
  const upper = event.toUpperCase();
  if (ENTRY_EVENTS.has(upper)) return BADGE.ENTRY;
  if (BLOCK_EVENTS.has(upper)) return BADGE.BLOCK;
  if (PENDING_EVENTS.has(upper)) return BADGE.PENDING;
  if (upper === 'REMOVE') return BADGE.REMOVE;
  if (INFO_EVENTS.has(upper)) return BADGE.INFO;
  return BADGE.REMOVE;
}

/**
 * Map a trade direction string to a semantic badge category.
 */
export function getDirectionBadgeCategory(direction) {
  if (direction === 'long') return BADGE.LONG;
  if (direction === 'short') return BADGE.SHORT;
  return null;
}
