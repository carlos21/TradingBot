import { describe, it, expect } from 'vitest';
import {
  BADGE,
  getEventBadgeCategory,
  getDirectionBadgeCategory,
} from '../../domain/decisionLog.js';

describe('decisionLog domain', () => {
  it('categorizes entry events', () => {
    expect(getEventBadgeCategory('ENTRY')).toBe(BADGE.ENTRY);
    expect(getEventBadgeCategory('VAT_CROSS_2')).toBe(BADGE.ENTRY);
    expect(getEventBadgeCategory('tsi_cross')).toBe(BADGE.ENTRY);
  });

  it('categorizes block events', () => {
    expect(getEventBadgeCategory('FILTER_BLOCK')).toBe(BADGE.BLOCK);
    expect(getEventBadgeCategory('TSI_INVALID')).toBe(BADGE.BLOCK);
  });

  it('categorizes pending events', () => {
    expect(getEventBadgeCategory('TRIGGER_SKIP')).toBe(BADGE.PENDING);
    expect(getEventBadgeCategory('LATCH_PENDING')).toBe(BADGE.PENDING);
  });

  it('categorizes info events', () => {
    expect(getEventBadgeCategory('VAT_REGIME')).toBe(BADGE.INFO);
    expect(getEventBadgeCategory('TSI_SWEEP')).toBe(BADGE.INFO);
  });

  it('falls back to remove badge', () => {
    expect(getEventBadgeCategory('UNKNOWN')).toBe(BADGE.REMOVE);
    expect(getEventBadgeCategory(null)).toBe(BADGE.REMOVE);
  });

  it('categorizes directions', () => {
    expect(getDirectionBadgeCategory('long')).toBe(BADGE.LONG);
    expect(getDirectionBadgeCategory('short')).toBe(BADGE.SHORT);
    expect(getDirectionBadgeCategory('')).toBeNull();
  });
});
