import { describe, it, expect } from 'vitest';
import { resolvePinnedPair, findInstrument } from '../../domain/instruments.js';

const instruments = [
  { symbol: 'MNQ', full_name: 'MNQ 09-26', point_value: 2 },
  { symbol: 'MES', full_name: 'MES 09-26', point_value: 5 },
];

describe('resolvePinnedPair', () => {
  it('returns the URL pair when it matches a known instrument symbol', () => {
    expect(resolvePinnedPair('MES', instruments)).toBe('MES');
    expect(resolvePinnedPair('MNQ', instruments)).toBe('MNQ');
  });

  it('returns null when the URL pair is unknown — no fallback', () => {
    expect(resolvePinnedPair('ES', instruments)).toBe(null);
  });

  it('returns null when there is no URL pair — no fallback', () => {
    expect(resolvePinnedPair(null, instruments)).toBe(null);
  });

  it('returns null when the catalog is empty', () => {
    expect(resolvePinnedPair(null, [])).toBe(null);
    expect(resolvePinnedPair('MNQ', [])).toBe(null);
  });

  it('treats a non-array catalog as empty', () => {
    expect(resolvePinnedPair('MNQ', undefined)).toBe(null);
  });
});

describe('findInstrument', () => {
  it('returns the instrument matching the symbol', () => {
    expect(findInstrument(instruments, 'MES')).toEqual(instruments[1]);
  });

  it('returns null when the symbol is not in the catalog', () => {
    expect(findInstrument(instruments, 'ES')).toBe(null);
  });

  it('returns null for a missing symbol or catalog', () => {
    expect(findInstrument(instruments, null)).toBe(null);
    expect(findInstrument(null, 'MNQ')).toBe(null);
  });
});
