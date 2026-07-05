/**
 * Pure formatting helpers shared across the UI.
 * No DOM dependencies except the escapeHtml helper which creates a
 * temporary element — this is safe to run in any JS environment.
 */

export function formatNumber(n) {
  return n !== undefined && n !== null ? n.toLocaleString() : '-';
}

export function formatPercent(n) {
  return n !== undefined && n !== null ? `${(n * 100).toFixed(1)}%` : '-';
}

export function formatCurrency(n) {
  if (n === undefined || n === null) return '-';
  const formatted = Math.abs(n).toFixed(2);
  const sign = n >= 0 ? '+' : '-';
  return `${sign}$${formatted}`;
}

export function formatSignedCurrency(n) {
  if (n === undefined || n === null) return '-';
  const formatted = Math.abs(n).toFixed(2);
  const sign = n >= 0 ? '+' : '-';
  return `${sign}$${formatted}`;
}

export function formatSignedR(n) {
  if (n === undefined || n === null) return '-';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}R`;
}

/**
 * Escape HTML entities in a string.
 * Uses a temporary DOM element when available; falls back to a simple
 * regex replacement in non-DOM environments (e.g. some test runners).
 */
export function escapeHtml(text) {
  if (text == null) return '';
  const str = String(text);
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
