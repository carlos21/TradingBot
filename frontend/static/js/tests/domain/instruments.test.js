import { describe, it, expect } from 'vitest';
import { resolvePinnedPair, findInstrument } from '../../domain/instruments.js';

const instruments = [
  { symbol: 'MNQ', full_name: 'MNQ 09-26', point_value: 2 },
  { symbol: 'MES', full_name: 'MES 09-26', point_value: 5 },
];

describe('resolvePinnedPair', () => {
  it('URL pair wins when it matches a known instrument symbol', () => {
    expect(resolvePinnedPair('MES', instruments, 'MNQ')).toBe('MES');
  });

  it('falls back to the default pair when the URL pair is unknown', () => {
    expect(resolvePinnedPair('ES', instruments, 'MNQ')).toBe('MNQ');
  });

  it('falls back to the default pair when there is no URL pair', () => {
    expect(resolvePinnedPair(null, instruments, 'MNQ')).toBe('MNQ');
  });

  it('falls back to the first instrument when there is no URL pair or default', () => {
    expect(resolvePinnedPair(null, instruments, null)).toBe('MNQ');
  });

  it('returns null when the catalog is empty and there is no default', () => {
    expect(resolvePinnedPair(null, [], null)).toBe(null);
    expect(resolvePinnedPair('MNQ', [], undefined)).toBe(null);
  });

  it('returns the default pair even when the catalog is empty', () => {
    expect(resolvePinnedPair(null, [], 'MNQ')).toBe('MNQ');
  });

  it('treats a non-array catalog as empty', () => {
    expect(resolvePinnedPair('MNQ', undefined, null)).toBe(null);
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
