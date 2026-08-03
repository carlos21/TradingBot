import { describe, it, expect } from 'vitest';
import {
  NY_SESSION,
  isInNewYorkSession,
  formatChartTime,
  formatNYTime,
  formatNYTimeFull,
  formatReplayDate,
} from '../../domain/time.js';

describe('time domain', () => {
  it('exports the NY session constants', () => {
    expect(NY_SESSION).toEqual({ from: { h: 9, m: 30 }, to: { h: 16, m: 0 } });
  });

  it('recognizes timestamps inside the NY session', () => {
    // 2024-01-16 14:30 UTC -> 09:30 ET
    expect(isInNewYorkSession(Date.parse('2024-01-16T14:30:00Z') / 1000)).toBe(true);
    // 2024-01-16 15:00 UTC -> 10:00 ET
    expect(isInNewYorkSession(Date.parse('2024-01-16T15:00:00Z') / 1000)).toBe(true);
  });

  it('rejects timestamps outside the NY session', () => {
    // 2024-01-16 04:00 UTC -> 23:00 previous day ET
    expect(isInNewYorkSession(Date.parse('2024-01-16T04:00:00Z') / 1000)).toBe(false);
    // 2024-01-16 21:00 UTC -> 16:00 ET (session end is inclusive, so 16:00 is in)
    expect(isInNewYorkSession(Date.parse('2024-01-16T21:00:00Z') / 1000)).toBe(true);
    // 2024-01-16 21:01 UTC -> 16:01 ET
    expect(isInNewYorkSession(Date.parse('2024-01-16T21:01:00Z') / 1000)).toBe(false);
  });

  it('handles invalid inputs gracefully', () => {
    expect(isInNewYorkSession(null)).toBe(false);
    expect(isInNewYorkSession(undefined)).toBe(false);
    expect(isInNewYorkSession(NaN)).toBe(false);
    expect(isInNewYorkSession(-Infinity)).toBe(false);
  });

  it('formats chart time', () => {
    const ts = Date.parse('2024-01-16T14:30:00Z') / 1000;
    const result = formatChartTime(ts);
    expect(result).toContain('9:');
    expect(result).toContain('30');
  });

  it('formats NY time and full NY time', () => {
    const ts = Date.parse('2024-01-16T14:30:00Z') / 1000;
    expect(formatNYTime(ts)).toMatch(/09:30:00/);
    expect(formatNYTimeFull(ts)).toMatch(/09:30:00/);
    expect(formatNYTimeFull(ts)).toContain('Jan');
  });

  it('formats replay date in NY timezone', () => {
    const ts = Date.parse('2024-06-16T18:00:00Z') / 1000;
    expect(formatReplayDate(ts)).toMatch(/2024-06-16 14:00/);
  });

  it('handles invalid replay date inputs', () => {
    expect(formatReplayDate(null)).toBe('');
    expect(formatReplayDate(undefined)).toBe('');
    expect(formatReplayDate(NaN)).toBe('');
  });
});
