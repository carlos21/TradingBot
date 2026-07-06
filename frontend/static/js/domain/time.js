/**
 * Pure time/session helpers for chart rendering.
 * No DOM or I/O dependencies.
 */

export const NY_SESSION = { from: { h: 9, m: 30 }, to: { h: 16, m: 0 } };

/**
 * Check whether a Unix timestamp (seconds) falls inside the New York
 * equity session (09:30–16:00 ET).
 *
 * @param {number} timestampSec
 * @returns {boolean}
 */
export function isInNewYorkSession(timestampSec) {
  if (timestampSec == null || !isFinite(timestampSec)) return false;

  const date = new Date(timestampSec * 1000);
  // Use America/New_York so the check is correct across DST.
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(date);

  let h = 0;
  let m = 0;
  for (const part of parts) {
    if (part.type === 'hour') h = parseInt(part.value, 10);
    if (part.type === 'minute') m = parseInt(part.value, 10);
  }

  const s = NY_SESSION;
  return (
    (h > s.from.h || (h === s.from.h && m >= s.from.m)) &&
    (h < s.to.h || (h === s.to.h && m <= s.to.m))
  );
}

/**
 * Format a Unix timestamp (seconds) as a compact time string in the
 * chart's display timezone (Etc/GMT+5, matching the existing chart).
 */
export function formatChartTime(timestampSec) {
  if (timestampSec == null || !isFinite(timestampSec)) return '';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'Etc/GMT+5',
    month: 'short',
    day: 'numeric',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestampSec * 1000));
}

/**
 * Format a Unix timestamp (seconds) as a full NY date/time string.
 */
export function formatNYTimeFull(timestampSec) {
  if (timestampSec == null || !isFinite(timestampSec)) return '';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    month: 'short',
    day: 'numeric',
  }).format(new Date(timestampSec * 1000));
}

/**
 * Format a Unix timestamp (seconds) as an NY time string (HH:MM:SS).
 */
export function formatNYTime(timestampSec) {
  if (timestampSec == null || !isFinite(timestampSec)) return '';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(timestampSec * 1000));
}
