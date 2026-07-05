import { describe, it, expect } from 'vitest';
import {
  RESULT,
  getTradeResultCategory,
  getMondayBasedDay,
  groupTradesByMonth,
  groupTradesByDay,
  sumPnl,
  buildWeeks,
} from '../../domain/calendar.js';

describe('calendar domain', () => {
  it('categorizes trade results', () => {
    expect(getTradeResultCategory({ result: 1.2 })).toBe(RESULT.WIN);
    expect(getTradeResultCategory({ result: -1.2 })).toBe(RESULT.LOSS);
    expect(getTradeResultCategory({ result: 0.2 })).toBe(RESULT.BREAKEVEN);
    expect(getTradeResultCategory({ result: -0.2 })).toBe(RESULT.BREAKEVEN);
    expect(getTradeResultCategory({ result_type: 'SP', result: 1.5 })).toBe(RESULT.SCALE_PARTIAL);
    expect(getTradeResultCategory({})).toBe(RESULT.UNKNOWN);
  });

  it('computes Monday-based day index', () => {
    // Local-date constructor avoids timezone ambiguity in tests.
    expect(getMondayBasedDay(new Date(2024, 0, 15))).toBe(0); // Monday
    expect(getMondayBasedDay(new Date(2024, 0, 20))).toBe(5); // Saturday
    expect(getMondayBasedDay(new Date(2024, 0, 21))).toBe(6); // Sunday
  });

  it('groups trades by month chronologically', () => {
    const trades = [
      { trade_id: 'a', entry_time: Date.parse('2024-02-15T10:00:00Z') / 1000, result: 1, pnl_usd: 100 },
      { trade_id: 'b', entry_time: Date.parse('2024-01-10T10:00:00Z') / 1000, result: -1, pnl_usd: -50 },
      { trade_id: 'c', entry_time: Date.parse('2024-02-16T10:00:00Z') / 1000, result: 1, pnl_usd: 75 },
    ];
    const months = groupTradesByMonth(trades);
    expect(months).toHaveLength(2);
    expect(months[0].key).toMatch(/^2024-01/);
    expect(months[1].key).toMatch(/^2024-02/);
    expect(months[1].stats.totalPnl).toBe(175);
    expect(months[1].stats.wins).toBe(2);
  });

  it('groups trades by day', () => {
    const month = {
      year: 2024,
      month: 0, // January
      trades: [
        { entry_time: Date.parse('2024-01-10T10:00:00Z') / 1000 },
        { entry_time: Date.parse('2024-01-10T15:00:00Z') / 1000 },
        { entry_time: Date.parse('2024-01-15T10:00:00Z') / 1000 },
      ],
    };
    const dayMap = groupTradesByDay(month);
    expect(dayMap.get(10)).toHaveLength(2);
    expect(dayMap.get(15)).toHaveLength(1);
  });

  it('sums P&L', () => {
    expect(sumPnl([{ pnl_usd: 100 }, { pnl_usd: -30 }, {}])).toBe(70);
  });

  it('builds weekday-only weeks', () => {
    // January 2024 starts on Monday, 31 days.
    const month = {
      year: 2024,
      month: 0,
      trades: [],
    };
    const weeks = buildWeeks(month);
    expect(weeks.length).toBeGreaterThan(0);
    for (const week of weeks) {
      expect(week).toHaveLength(5);
    }
  });
});
