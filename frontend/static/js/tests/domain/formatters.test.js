import { describe, it, expect } from 'vitest';
import {
  formatNumber,
  formatPercent,
  formatCurrency,
  formatSignedR,
  escapeHtml,
} from '../../domain/formatters.js';

describe('formatters domain', () => {
  it('formats numbers', () => {
    expect(formatNumber(1234.5)).toBe('1,234.5');
    expect(formatNumber(0)).toBe('0');
    expect(formatNumber(null)).toBe('-');
    expect(formatNumber(undefined)).toBe('-');
  });

  it('formats percentages', () => {
    expect(formatPercent(0.5123)).toBe('51.2%');
    expect(formatPercent(null)).toBe('-');
  });

  it('formats currency with sign', () => {
    expect(formatCurrency(123.456)).toBe('+$123.46');
    expect(formatCurrency(-50)).toBe('-$50.00');
    expect(formatCurrency(null)).toBe('-');
  });

  it('formats R values with sign', () => {
    expect(formatSignedR(1.23)).toBe('+1.23R');
    expect(formatSignedR(-0.5)).toBe('-0.50R');
  });

  it('escapes HTML', () => {
    expect(escapeHtml('<script>alert("x")</script>')).toBe(
      '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;'
    );
    expect(escapeHtml(null)).toBe('');
  });
});
